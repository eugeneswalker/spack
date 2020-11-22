# Copyright 2013-2020 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import spack.config
import spack.modules
import spack.modules.common
import llnl.util.tty as tty


def _for_each_enabled(spec, method_name):
    """Calls a method for each enabled module"""
    enabled = spack.config.get('modules:enable')
    if not enabled:
        tty.debug('NO MODULE WRITTEN: list of enabled module files is empty')
        return

    #import spack.hooks.sbang as sbang
    import sys
    #import spack.hooks
    print("\n\nINSIDE hooks module_file_generation.py:_for_each_enabled(): \nsbang loaded = {}\n{}\n\n".format("spack.hooks.sbang" in sys.modules, "\n".join(sys.path)))

    for name in enabled:
        generator = spack.modules.module_types[name](spec)
        try:
            getattr(generator, method_name)()
        except RuntimeError as e:
            msg = 'cannot perform the requested {0} operation on module files'
            msg += ' [{1}]'
            tty.warn(msg.format(method_name, str(e)))


def post_install(spec):
    #import spack.hooks.sbang as sbang
    import sys
    print("\n\nINSIDE hooks module_file_generation.py:post_install(): \nsbang loaded = {}\n{}\n\n".format("spack.hooks.sbang" in sys.modules, "\n".join(sys.path)))
    _for_each_enabled(spec, 'write')


def post_uninstall(spec):
    _for_each_enabled(spec, 'remove')
