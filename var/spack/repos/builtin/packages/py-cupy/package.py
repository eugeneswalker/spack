# Copyright 2013-2023 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import *


class PyCupy(PythonPackage, CudaPackage, ROCmPackage):
    """CuPy is an open-source array library accelerated with
    NVIDIA CUDA. CuPy provides GPU accelerated computing with
    Python. CuPy uses CUDA-related libraries including cuBLAS,
    cuDNN, cuRand, cuSolver, cuSPARSE, cuFFT and NCCL to make
    full use of the GPU architecture."""

    homepage = "https://cupy.dev/"
    pypi = "cupy/cupy-8.0.0.tar.gz"

    version("11.2.0", sha256="c33361f117a347a63f6996ea97446d17f1c038f1a1f533e502464235076923e2")
    version("11.3.0", sha256="d057cc2f73ecca06fae8b9c270d9e14116203abfd211a704810cc50a453b4c9e")
    version("11.4.0", sha256="03d52b2626e02a3a2b46d714c1cd03e702c8fe33915fcca6ed8de5c539964f49")
    version("11.5.0", sha256="4bc8565bded22cc89b210fd9fb48a5d5316f30701e12bb23852a60314e1f9f6e")
    version("11.6.0", sha256="53dbb840072bb32d4bfbaa6bfa072365a30c98b1fcd1f43e48969071ad98f1a7")
    version("12.0.0", sha256="61ddbbef73d50d606bd5087570645f3c91ec9176c2566784c1d486d6a3404545")
    version("12.1.0", sha256="f6d31989cdb2d96581da12822e28b102f29e254427195c2017eac327869b7320")

    depends_on("python@3.7:", when="@:11", type=("build", "run"))
    depends_on("python@3.8:", when="@12:", type=("build", "run"))
    depends_on("py-setuptools", type="build")
    depends_on("py-cython@0.29.22:2", type="build")
    depends_on("py-fastrlock@0.5:", type=("build", "run"))
    depends_on("py-numpy@1.20:1.25", when="@:11", type=("build", "run"))
    depends_on("py-numpy@1.20:1.26", when="@12:", type=("build", "run"))
    depends_on("py-scipy@1.6:1.12", type=("build", "run"))
    depends_on("cuda@:11.9", when="@:11 +cuda")
    depends_on("cuda@:12.1", when="@12: +cuda")
    depends_on("nccl", when="+cuda")
    depends_on("cudnn", when="+cuda")
    depends_on("cutensor", when="+cuda")
    depends_on("hipcub", when="+rocm", type=("build", "link", "run"))
    depends_on("hipblas", when="+rocm", type=("build", "link", "run"))
    depends_on("hiprand", when="+rocm", type=("build", "link", "run"))
    depends_on("hipsparse", when="+rocm", type=("build", "link", "run"))
    depends_on("hipfft", when="+rocm", type=("build", "link", "run"))
    depends_on("roctracer-dev", when="+rocm", type=("build", "link", "run"))
    depends_on("rocprofiler-dev", when="+rocm", type=("build", "link", "run"))

    conflicts("~cuda ~rocm")

    def setup_build_environment(self, env):
        env.set("CUPY_NUM_BUILD_JOBS", make_jobs)
        if self.spec.satisfies("+cuda"):
            cuda_arch = self.spec.variants["cuda_arch"].value
            arch_str = ";".join("arch=compute_{0},code=sm_{0}".format(i) for i in cuda_arch)
            env.set("CUPY_NVCC_GENERATE_CODE", arch_str)
        if self.spec.satisfies("+rocm"):
            env.set("ROCM_HOME", self.spec["hip"].prefix)
            env.set("CUPY_INSTALL_USE_HIP", 1)
#            env.set("HCC_AMDGPU_TARGET", self.spec.variants["amdgpu_target"].value)
