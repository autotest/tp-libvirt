#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#

import os
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import virsh

from virttest.utils_test import libvirt

from provider.numa import numa_base


def modify_numad_executable_file(numatest_obj):
    """
    Modify numad executable file

    :param numatest_obj: NumaTest object
    """
    dest_dir = os.path.join(data_dir.get_tmp_dir(), 'numad')
    numad_path_origin = '/usr/bin/numad'
    shutil.copyfile(numad_path_origin, dest_dir)
    new_numad_content = """
#! /bin/sh
exit 1"""

    with open(numad_path_origin, 'w') as fp:
        fp.write(new_numad_content)

    process.run('restorecon %s' % numad_path_origin,  shell=True)
    numatest_obj.test.log.debug("Backup the numad file to %s", dest_dir)
    return dest_dir


def setup_default(numatest_obj):
    """
    Default setup function for the test

    :param numatest_obj: NumaTest object
    """
    dest_dir = modify_numad_executable_file(numatest_obj)
    numatest_obj.test.params['numad_backup'] = dest_dir
    numatest_obj.setup()
    numatest_obj.test.log.debug("Step: setup is done")


def run_default(numatest_obj):
    """
    Default run function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.test.log.debug("Step: prepare vm xml")
    vmxml = numatest_obj.prepare_vm_xml()
    numatest_obj.test.log.debug("Step: define vm")
    ret = virsh.define(vmxml.xml, **numatest_obj.virsh_dargs)
    numatest_obj.test.log.debug("Step: start vm and check result")
    numatest_obj.virsh_dargs.update({'ignore_status': True})
    ret = virsh.start(numatest_obj.vm.name, **numatest_obj.virsh_dargs)
    err_msg_expected = numatest_obj.produce_expected_error()
    libvirt.check_result(ret, expected_fails=err_msg_expected)


def teardown_default(numatest_obj):
    """
    Default teardown function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.teardown()
    numad_backup = numatest_obj.test.params['numad_backup']
    numad_path_origin = '/usr/bin/numad'
    with open(numad_path_origin) as fp:
        numatest_obj.test.log.debug("Before recovering, "
                                    "%s is%s", numad_path_origin, fp.read())
    shutil.copyfile(numad_backup, numad_path_origin)
    process.run('restorecon %s' % numad_path_origin,  shell=True)
    numatest_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa memory binding with emulator thread pin
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
