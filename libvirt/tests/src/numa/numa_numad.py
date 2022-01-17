import logging as log
import os.path
import shutil

from avocado.core import exceptions
from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_xml
from virttest import virt_vm


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def action_for_numad_file(numad_path='/usr/bin/numad', action='recover'):
    """
    Backup and update or recover numad binary

    :param numad_path: system path to numad binary
    :param action: action to perform with numad file. It can be either "recover"
    for recovering the binary to default on or "update" to backup the binary and
    change it's content.
    """
    tmp_dir = data_dir.get_data_dir()
    src = os.path.join(tmp_dir, 'numad') if action == 'recover' else numad_path
    dst = numad_path if action == 'recover' else os.path.join(tmp_dir, 'numad')
    if not os.path.exists(src):
        exceptions.TestError('The numad file does not exists on the provided '
                             'path: {}'.format(src))
    try:
        # Copy numad so it can be overwritten/recovered during/after the test:
        shutil.copy(src, dst)
    except Exception as e:
        exceptions.TestError('Copy numad file from: {} to: {} failed due to:{}'.
                             format(src, dst, e))
    else:
        logging.info('File numad on path:{} successfully overwritten by:{}.'.
                     format(dst, src))
    if action == 'update':
        # Overwrite content of numad binary:
        ret = process.run("echo -e '#!/bin/sh \n exit 1' > {}".
                          format(numad_path), shell=True)
        if ret.exit_status:
            exceptions.TestError('Cannot edit numad file due to: {}'.
                                 format(ret.stderr_text))
        # For info purposes:
        process.run("cat {}".format(numad_path))
        # Run restorecon:
        ret = process.run("restorecon {}".format(numad_path), shell=True)
        if ret.exit_status:
            exceptions.TestError('Cannot restorecon numad file due to: {}'.
                                 format(ret.stderr_text))


def run(test, params, env):
    """
    Test Live update the numatune nodeset and memory can spread to other node
    automatically.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    err_msg = params.get("err_msg", '')
    try:
        if vm.is_alive():
            vm.destroy()
        action_for_numad_file(numad_path='/usr/bin/numad', action='update')
        memory_mode = params.get('memory_mode', 'strict')
        memory_placement = params.get('memory_placement', 'auto')
        numa_memory = {'mode': memory_mode,
                       'placement': memory_placement}
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        vm.start()
    except virt_vm.VMStartError as detail:
        if err_msg in str(detail):
            logging.info('Expected VM start failure.')
        else:
            test.fail('The VM cannot be started, but from different reason than'
                      ' expected:{}. The expected error is: {}.'.
                      format(detail, err_msg))
    except (exceptions.TestFail, exceptions.TestCancel):
        raise
    except Exception as e:
        test.error("Unexpected failure: {}.".format(e))
    finally:
        backup_xml.sync()
        action_for_numad_file(numad_path='/usr/bin/numad', action='recover')
