import logging as log
import os
import tempfile

from virttest import data_dir
from virttest import virsh               # pylint: disable=W0611

from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Run tests for {hypervisor_}cpu_{baseline,compare} with different xmls
    Those xmls include:
        domcapabilities xml from two different hosts
        capabilities xml from two different hosts
        cpu xml within capabilities xml from two different hosts
        cpu xml within domain dumpxml from two different hosts
    """

    file_path = params.get('file_path')
    file_xml_declaration = params.get('file_xml_declaration')
    virsh_function = eval(params.get('virsh_function'))
    err_msg = params.get('err_msg')
    out_msg = params.get('out_msg')
    data_file = tempfile.mktemp(dir=data_dir.get_tmp_dir())
    file_content = None
    file_path = os.path.join(os.path.dirname(__file__), file_path)
    if file_xml_declaration:
        with open(file_path) as path_fd:
            file_content = path_fd.read()
        with open(data_file, 'w+') as data_fd:
            data_fd.write(file_xml_declaration)
            data_fd.write('\n')
            data_fd.write(file_content)
    else:
        data_file = file_path

    with open(data_file) as fd:
        logging.debug("The XML file under test:\n%s", fd.read())

    if virsh_function:
        ret = virsh_function(data_file, ignore_status=True, debug=True)
        if not ret.exit_status and out_msg:
            libvirt.check_result(ret, expected_match=out_msg)
        else:
            libvirt.check_result(ret,
                                 expected_fails=err_msg,
                                 check_both_on_error=True)
