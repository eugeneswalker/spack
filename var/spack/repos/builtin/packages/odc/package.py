# Copyright 2013-2023 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import *


class Odc(CMakePackage):
    """ECMWF encoding and decoding of observational data in ODB2 format."""

    homepage = "https://github.com/ecmwf/odc"
    url = "https://github.com/ecmwf/odc/archive/refs/tags/1.3.0.tar.gz"

    maintainers("skosukhin", "climbfuji")

    version("1.4.6", sha256="ff99d46175e6032ddd0bdaa3f6a5e2c4729d24b698ba0191a2a4aa418f48867c")
    version("1.4.5", sha256="8532d0453531d62e1f15791d1c5c96540b842913bd211a8ef090211eaf4cccae")
    version("1.4.4", sha256="65cb7b491566d3de14b66741544360f20eaaf1a6d5a24af7d8b939dd50e26431")
    version("1.4.2", sha256="19572e93238c1531bcf0f7966f0d2342a0400f5fe9deb934a384228f895909c9")
    version("1.4.1", sha256="e79707c8cd951a3d79439013d43d6b2888956a34e9b76ce416bece5262b77d93")
    version("1.3.0", sha256="97a4f10765b341cc8ccbbf203f5559cb1b838cbd945f48d4cecb1bc4305e6cd6")


    variant("fortran", default=False, description="Enable the Fortran interface")

    depends_on("ecbuild@3.4:", type="build")
    depends_on("cmake@3.12:", type="build")

    depends_on("eckit@1.4:+sql")

    def cmake_args(self):
        args = [
            self.define_from_variant("ENABLE_FORTRAN", "fortran"),
            # The tests download additional data (~650MB):
            self.define("ENABLE_TESTS", self.run_tests),
        ]
        # https://github.com/JCSDA/spack-stack/issues/585
        if self.spec.satisfies("%apple-clang@14.0.3"):
            args.append(self.define("CMAKE_C_FLAGS_RELEASE", "-O1"))
            args.append(self.define("CMAKE_CXX_FLAGS_RELEASE", "-O1"))
            args.append(self.define("CMAKE_C_FLAGS_RELWITHDEBINFO", "-O1"))
            args.append(self.define("CMAKE_CXX_FLAGS_RELWITHDEBINFO", "-O1"))
        return args
