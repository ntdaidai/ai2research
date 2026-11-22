"""Tiny CUDA Driver API kernel, Python stdlib only. Not a research benchmark."""
import ctypes as C
import json
import os
import time
from pathlib import Path

PTX = br"""
.version 6.0
.target sm_30
.address_size 64
.visible .entry increment(.param .u64 data, .param .u32 n, .param .u32 amount) {
 .reg .pred p;
 .reg .b32 r<8>;
 .reg .b64 rd<4>;
 ld.param.u64 rd1, [data];
 ld.param.u32 r1, [n];
 ld.param.u32 r6, [amount];
 mov.u32 r2, %tid.x;
 mov.u32 r3, %ctaid.x;
 mov.u32 r4, %ntid.x;
 mad.lo.s32 r2, r3, r4, r2;
 setp.ge.u32 p, r2, r1;
 @p bra DONE;
 mul.wide.u32 rd2, r2, 4;
 add.s64 rd3, rd1, rd2;
 ld.global.u32 r5, [rd3];
 add.u32 r5, r5, r6;
 st.global.u32 [rd3], r5;
DONE:
 ret;
}
"""


def main():
    config = json.loads(Path("config.json").read_text())
    amount = int(config.get("increment", 1))
    if amount not in (1, 2):
        raise ValueError("Smoke increment must be 1 or 2")
    n = 4096
    cuda = C.CDLL("libcuda.so.1")
    signatures = {
        "cuInit": [C.c_uint], "cuDriverGetVersion": [C.POINTER(C.c_int)],
        "cuDeviceGetByPCIBusId": [C.POINTER(C.c_int), C.c_char_p],
        "cuCtxCreate_v2": [C.POINTER(C.c_void_p), C.c_uint, C.c_int],
        "cuCtxDestroy_v2": [C.c_void_p], "cuCtxSynchronize": [],
        "cuModuleLoadData": [C.POINTER(C.c_void_p), C.c_void_p],
        "cuModuleGetFunction": [C.POINTER(C.c_void_p), C.c_void_p, C.c_char_p],
        "cuModuleUnload": [C.c_void_p],
        "cuMemAlloc_v2": [C.POINTER(C.c_uint64), C.c_size_t], "cuMemFree_v2": [C.c_uint64],
        "cuMemcpyHtoD_v2": [C.c_uint64, C.c_void_p, C.c_size_t],
        "cuMemcpyDtoH_v2": [C.c_void_p, C.c_uint64, C.c_size_t],
        "cuLaunchKernel": [C.c_void_p] + [C.c_uint] * 7 + [C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p],
    }
    for name, signature in signatures.items():
        getattr(cuda, name).argtypes = signature
        getattr(cuda, name).restype = C.c_int

    def call(name, *args):
        code = getattr(cuda, name)(*args)
        if code:
            raise RuntimeError(name + " CUDA error " + str(code))

    context, module, pointer = C.c_void_p(), C.c_void_p(), C.c_uint64()
    call("cuInit", 0)
    version, device = C.c_int(), C.c_int()
    call("cuDriverGetVersion", C.byref(version))
    pci = os.environ["HARNESS_GPU_PCI_BUS_ID"][-12:]
    call("cuDeviceGetByPCIBusId", C.byref(device), pci.encode())
    try:
        call("cuCtxCreate_v2", C.byref(context), 0, device)
        image = C.create_string_buffer(PTX)
        call("cuModuleLoadData", C.byref(module), C.cast(image, C.c_void_p))
        function = C.c_void_p()
        call("cuModuleGetFunction", C.byref(function), module, b"increment")
        values = (C.c_uint32 * n)(*range(n))
        call("cuMemAlloc_v2", C.byref(pointer), C.sizeof(values))
        call("cuMemcpyHtoD_v2", pointer, values, C.sizeof(values))
        size_arg, amount_arg = C.c_uint32(n), C.c_uint32(amount)
        params = (C.c_void_p * 3)(C.cast(C.byref(pointer), C.c_void_p), C.cast(C.byref(size_arg), C.c_void_p), C.cast(C.byref(amount_arg), C.c_void_p))
        started = time.perf_counter()
        call("cuLaunchKernel", function, (n + 127) // 128, 1, 1, 128, 1, 1, 0, None, params, None)
        call("cuCtxSynchronize")
        elapsed = (time.perf_counter() - started) * 1000
        call("cuMemcpyDtoH_v2", values, pointer, C.sizeof(values))
        error = max(abs(values[i] - (i + amount)) for i in range(n))
        if error:
            raise AssertionError("GPU output differs from exact CPU reference")
        document = {"metrics": {"gpu_verified": 1, "elements_checked": n, "max_abs_error": error,
                                "result_sum": sum(values), "launch_and_sync_ms": elapsed, "allocated_buffer_bytes": C.sizeof(values)},
                    "artifacts": ["sample.json"], "evidence_kind": "synthetic_gpu_toolchain_test"}
        Path("sample.json").write_text(json.dumps({"first_eight": list(values[:8]), "increment": amount,
                                                   "gpu_uuid": os.environ["HARNESS_GPU_UUID"], "driver_version": version.value}))
        Path("metrics.json").write_text(json.dumps(document))
        print(json.dumps(document), flush=True)
        # Keep the context briefly for sampled memory telemetry, without GPU work.
        time.sleep(0.6)
    finally:
        if pointer.value:
            call("cuMemFree_v2", pointer)
        if module.value:
            call("cuModuleUnload", module)
        if context.value:
            call("cuCtxDestroy_v2", context)


if __name__ == "__main__":
    main()
