# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def prepare_c_file(tmp_c_file):
    outputlines = """
    #include <stdio.h>
    #include <stdlib.h>
    #include <libvirt/libvirt.h>
    #define SIZE 10
    int main(int argc, char **argv) {
        virConnectPtr conn = NULL;
        conn = virConnectOpenReadOnly("qemu:///system");
        char **names = (char **) calloc(sizeof(const char *), SIZE);
        for (int i = 0; i < SIZE; ++i) {
            *(names + i) =(char *) calloc(sizeof(char), SIZE);
        }
        virConnectListInterfaces(conn, names, atoi(argv[1]));
        return 0;
    }
    """
    with open(tmp_c_file, 'w') as output:
        output.writelines(outputlines)
    return tmp_c_file


def run(test, params, env):
    """
    Test virConnectListInterfaces will not crash when 0 number of interfaces is input.
    """
    def setup_test():
        """
        Compile the code virConnectListInterfaces.c and get the executable file path.
        """
        test.log.info("TEST_SETUP: Prepare the C code and compile.")
        utils_package.package_install(["gcc", "libvirt-devel"])

        prepare_c_file(tmp_c_file)
        result = process.run("gcc `pkg-config --libs libvirt` %s -g -o %s" % (tmp_c_file, tmp_exe_file), shell=True)
        if result.exit_status:
            test.fail("Compile C file failed: %s" % result.stderr_text.strip())

    def run_test():
        """
        Test virConnectListInterfaces will not crash when given 0 number of interfaces.
        """
        test.log.info("TEST_STEP1：Execute to check any virinterfaced crash")
        result = process.run("%s %s " % (tmp_exe_file, number_interfaces), shell=True)
        if result.exit_status:
            test.fail("virConnectListInterfaces API test when %s number of interfaces failed: %s" % (
                number_interfaces, result.stderr_text.strip()))

        test.log.info("TEST_STEP2: Check if exist coredump file.")
        res = process.run("coredumpctl list", shell=True, ignore_status=True)
        libvirt.check_result(res, expected_fails='No coredumps found',
                             check_both_on_error=True)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        if os.path.exists(tmp_exe_file):
            os.remove(tmp_exe_file)
        if os.path.exists(tmp_c_file):
            os.remove(tmp_c_file)
        bkxml.sync()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    number_interfaces = params.get("number_interfaces")
    tmp_c_file = params.get("tmp_c_file", "/tmp/virConnectListInterfaces.c")
    tmp_exe_file = params.get("tmp_exe_file", "/tmp/exe_file")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
