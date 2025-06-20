import datetime
import logging
import re

from avocado.utils import process
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.save import save_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test save vm to block device
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    namespaces = params.get('namespaces')
    expect_label = params.get('expect_label')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        selinux_status = passt.ensure_selinux_enforcing()
        # Record time for comparison avc log when finished
        start_time = datetime.datetime.now()
        unix_ts_start = start_time.timestamp()
        LOG.debug('Start time is: %s', start_time)
        if namespaces:
            qemu_conf.namespaces = eval(namespaces)
            libvirtd.restart()

        iscsi_dev = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        save_path = iscsi_dev
        LOG.debug(f'ISCSI device / save path: {iscsi_dev}')

        vm.start()
        pid_ping, upsince = save_base.pre_save_setup(vm)

        process.run(f'ls -lZ {save_path}')
        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        label_output = process.run(f'ls -lZ {save_path}').stdout_text
        match = re.search(expect_label, label_output)
        if not all([0 <= int(x) < 1024 for x in [match.group(1), match.group(2)]]):
            test.error(f'label of saved file not correct: {label_output}')

        virsh.restore(save_path, **VIRSH_ARGS)

        LOG.debug(f'VM state after restore: {vm.state()}')
        if vm.state() != 'running':
            test.fail(f'VM should be running after restore, not {vm.state()}')
        # Record time for comparison avc log
        end_time = datetime.datetime.now()
        unix_ts_end = end_time.timestamp()
        LOG.debug('End time is: %s', end_time)
        avc_denied_log = process.run("grep -E 'avc:.*denied' /var/log/audit/audit.log",
                                     ignore_status=True).stdout_text.strip()
        # Filter matching lines
        filtered_lines = []
        for line in avc_denied_log.splitlines():
            match = re.search(r"audit\((\d+\.\d+):\d+\)", line)
            if match:
                log_ts = float(match.group(1))
                if unix_ts_start <= log_ts <= unix_ts_end:
                    filtered_lines.append(line.strip())
        if filtered_lines:
            test.fail(f'Got avc denied:\n{filtered_lines}')
        save_base.post_save_check(vm, pid_ping, upsince)
        virsh.shutdown(vm_name, **VIRSH_ARGS)
    finally:
        qemu_conf.restore()
        libvirtd.restart()
        bkxml.sync()
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        utils_selinux.set_status(selinux_status)
