# Copyright 2013-2021 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack import *


class Datatransferkit(CMakePackage):
    """DataTransferKit is an open-source software library of
    parallel solution transfer services for multiphysics simulations"""

    homepage = "https://datatransferkit.readthedoc.io"
    url      = "https://github.com/ORNL-CEES/DataTransferKit/archive/3.1-rc2.tar.gz"
    git      = "https://github.com/ORNL-CEES/DataTransferKit.git"

    maintainers = ['Rombur']

    version('master', branch='master', submodules=True)
    version('3.1-rc2', commit='1abc1a43b33dffc7a16d7497b4185d09d865e36a', submodules=True)

    variant('external-arborx', default=False,
            description='use an external ArborX library instead of the submodule')
    variant('openmp', default=False, description='enable OpenMP backend')
    variant('serial', default=True, description='enable Serial backend (default)')
    variant('shared', default=True,
            description='enable the build of shared lib')

    depends_on('arborx@1.0:', when='+external-arborx')
    depends_on('cmake', type='build')
    depends_on('trilinos+intrepid2+shards~dtk', when='+serial')
    depends_on('trilinos+intrepid2+shards+openmp~dtk', when='+openmp')
    depends_on('trilinos+stratimikos+belos', when='@master')
    depends_on('trilinos@13:13.99', when='@3.1-rc2')

    def cmake_args(self):
        spec = self.spec

        options = [
            self.define_from_variant('BUILD_SHARED_LIBS', 'shared'),
            '-DDataTransferKit_ENABLE_DataTransferKit=ON',
            self.define_from_variant('DataTransferKit_ENABLE_ArborX_TPL', 'external-arborx'),
            '-DDataTransferKit_ENABLE_TESTS=OFF',
            '-DDataTransferKit_ENABLE_EXAMPLES=OFF',
            '-DCMAKE_CXX_EXTENSIONS=OFF',
            '-DCMAKE_CXX_STANDARD=14',
        ]

        if '+openmp' in spec:
            options.append('-DDataTransferKit_ENABLE_OpenMP=ON')

        return options
