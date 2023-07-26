import logging
import os

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import base
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_graph_passwd(test, output, passwd, expect=True):
    """
    Check whether output meets expectation

    :param test: test instance
    :param output: output string
    :param passwd: passwd to check
    :param expect: expect or not, defaults to True
    """
    expect_prefix = "" if expect else "not"
    found = passwd in output
    actual_prefix = "" if found else "not"
    msg = f'Expect {expect_prefix} found graphic password, '\
          f'actually {actual_prefix} found it.'
    if found == expect:
        LOG.debug(msg)
    else:
        test.fail(msg)


def check_osxml(test, vmxml, output, wrap):
    """
    Check whether os xml equals to the output

    :param test: test instance
    :param vmxml: vmxml instance
    :param output: output string
    :param wrap: wrap or not
    """
    if wrap:
        wrap_xml = base.LibvirtXMLBase()
        wrap_xml.xml = output
        children = list(wrap_xml.xmltreefile.get_parent_map('nodes').keys())
        if len(children) != 1:
            test.fail('Found more than 1 elements in wrapped xml, '
                      'there should only be "os"')
    osxml = vmxml.os
    dump_osxml = vm_xml.VMOSXML()
    dump_osxml.xml = wrap_xml.get_section_string('/os') if wrap else output

    if osxml == dump_osxml:
        LOG.debug('osxml is identical with the output of save-image-dumpxml')
    else:
        test.fail('osxml changed after save-image-dumpxml')


def run(test, params, env):
    """
    Test virsh save-image-dumpxml with options
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    virsh_options = params.get('virsh_options', '')
    options = params.get('options', '')
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    graph_passwd = utils_misc.generate_random_string(6)
    expect_graph_pw = 'yes' == params.get('expect_graph_pw')
    check_sec = 'yes' == params.get('check_sec')
    check_os = 'yes' == params.get('check_os')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vm_xml.VMXML.set_graphics_attr(vm_name, {'passwd': graph_passwd})

        vm.start()
        vm.wait_for_login().close()

        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        result = virsh.save_image_dumpxml(save_path, options=options,
                                          debug=True, virsh_opt=virsh_options)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            libvirt.check_result(result, error_msg)

        else:
            output_xml = result.stdout_text
            if check_sec:
                check_graph_passwd(test, output_xml, graph_passwd,
                                   expect_graph_pw)

            if check_os:
                wrap = True if '--wrap' in options else False
                check_osxml(test, vmxml, output_xml, wrap)

    finally:
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
