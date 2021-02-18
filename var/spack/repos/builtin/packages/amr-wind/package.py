from spack import *


class AmrWind(CMakePackage):
    """AMR-Wind is a massively parallel, block-structured adaptive-mesh, incompressible flow sover for wind turbine and wind farm simulations. """

    homepage = "https://github.com/Exawind/amr-wind"
    git      = "https://github.com/exawind/amr-wind.git"

    maintainers = ['jrood-nrel']

    tags = ['ecp', 'ecp-apps']

    version('main', branch='main')

    variant('mpi', default=False,
            description='Enable MPI support')
    variant('tests', default=False,
            description='Enable tests')
    variant('fortran', default=False,
            description='Build fortran interfaces')
    variant('cuda', default=False,
            description='Enable CUDA build')
    variant('openmp', default=False,
            description='Enable OpenMP for CPU builds')

    conflicts('+openmp', when='+cuda')

    depends_on('amrex@amr-wind +particles +pic')
    depends_on('mpi', when='+mpi')
    depends_on('cuda', when='+cuda')

    @run_before('cmake')
    def add_submodules(self):
        #if self.run_tests or '+wind-utils' in self.spec:
        git = which('git')
        git('submodule', 'update', '--init', '--recursive')

    def cmake_args(self):
        spec = self.spec
        options = []

        options.extend([
            '-DCMAKE_C_COMPILER=%s' % spec['mpi'].mpicc,
            '-DCMAKE_CXX_COMPILER=%s' % spec['mpi'].mpicxx,
            '-DCMAKE_Fortran_COMPILER=%s' % spec['mpi'].mpifc,
            '-DMPI_C_COMPILER=%s' % spec['mpi'].mpicc,
            '-DMPI_CXX_COMPILER=%s' % spec['mpi'].mpicxx,
            '-DMPI_Fortran_COMPILER=%s' % spec['mpi'].mpifc,
            '-DAMR_WIND_ENABLE_MASA=OFF',
            '-DAMR_WIND_ENABLE_NETCDF=OFF'])

        vs = ["mpi", "tests", "fortran", "cuda", "openmp"]
        for v in vs:
            opt = self.define_from_variant("AMR_WIND_ENABLE_{v}".format(v=v), v)
            options.append(opt)

        options.extend([
            '-DAMR_WIND_USE_INTERNAL_AMREX=OFF',
            '-DAMREX_DIR={amrex_dir}'.format(amrex_dir=spec['amrex'].prefix)
        ])
        
#        if '+mpi' in spec:
#            options.append('-DAMR_WIND_ENABLE_MPI=ON')
#        else:
#            options.append('-DAMR_WIND_ENABLE_MPI=OFF')
#
#        if '+tests' in spec:
#            options.append('-DAMR_WIND_ENABLE_TESTS=ON')
#        else:
#            options.append('-DAMR_WIND_ENABLE_TESTS=OFF')
#
#        if '+fortran' in spec:
#            options.append('-DAMR_WIND_ENABLE_FORTRAN=ON')
#        else:
#            options.append('-DAMR_WIND_ENABLE_FORTRAN=OFF')
#
#        if '+cuda' in spec:
#            options.append('-DAMR_WIND_ENABLE_CUDA=ON')
#        else:
#            options.append('-DAMR_WIND_ENABLE_CUDA=OFF')
#
#        if '+openmp' in spec:
#            options.append('-DAMR_WIND_ENABLE_OPENMP=ON')
#        else:
#            options.append('-DAMR_WIND_ENABLE_OPENMP=OFF')

        return options
