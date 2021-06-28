import re
import os
import logging
import tempfile

from avocado.utils import process
from virttest import data_dir
from virttest import utils_misc
from virttest.utils_v2v import multiple_versions_compare
from virttest.utils_v2v import params_get


def run(test, params, env):
    """
    Basic nbdkit tests
    """
    checkpoint = params.get('checkpoint')
    version_requried = params.get('version_requried')

    def test_filter_stats_fd_leak():
        """
        check if nbdkit-stats-filter leaks an fd
        """
        tmp_logfile = os.path.join(data_dir.get_tmp_dir(), "nbdkit-test.log")
        cmd = """
nbdkit -U - --filter=log --filter=stats sh - \
  logfile=/dev/null statsfile=/dev/null \
  --run 'qemu-io -r -f raw -c "r 0 1" $nbd' <<\EOF
  case $1 in
    get_size) echo 1m;;
    pread) ls -l /proc/$$/fd > %s
      dd iflag=count_bytes count=$3 if=/dev/zero || exit 1 ;;
    *) exit 2 ;;
  esac
EOF
""" % tmp_logfile

        process.run(cmd, shell=True)

        count = 0
        with open(tmp_logfile) as fd:
            ptn = r'(\d+)\s+->\s+\'(pipe|socket):'
            cont = fd.read()
            logging.debug('all fds:\n%s', cont)
            for i, _ in re.findall(ptn, cont):
                if int(i) > 2:
                    count += 1
        if count > 0:
            test.fail('nbdkit-stats-filter leaks %d fd' % count)

    def test_has_run_againt_vddk7_0():
        """
        check if nbdkit --run + vddk + esx7.0 works.
        """
        from virttest.utils_pyvmomi import VSphereConnection, vim

        vm_name = params_get(params, "main_vm")
        if not vm_name:
            test.error('No VM specified')
        # vsphere server's host name or IP address
        vsphere_host = params_get(params, "vsphere_host")
        vsphere_user = params_get(params, "vsphere_user", 'root')
        # vsphere password
        vsphere_pwd = params_get(params, "vsphere_pwd")
        vsphere_passwd_file = params_get(
            params, "vpx_passwd_file", '/tmp/v2v_vpx_passwd')
        with open(vsphere_passwd_file, 'w') as fd:
            fd.write(vsphere_pwd)

        # get vm and file's value
        connect_args = {
            'host': vsphere_host,
            'user': vsphere_user,
            'pwd': vsphere_pwd}
        with VSphereConnection(**connect_args) as conn:
            conn.target_vm = vm_name
            nbdkit_vm_name = 'moref=' + \
                str(conn.target_vm).strip('\'').split(':')[1]
            nbdkit_file = conn.get_hardware_devices(
                dev_type=vim.vm.device.VirtualDisk)[0].backing.fileName

        # vddk_libdir
        vddk_libdir_src = params_get(params, "vddk_libdir_src")
        with tempfile.TemporaryDirectory(prefix='vddklib_') as vddk_libdir:
            utils_misc.mount(vddk_libdir_src, vddk_libdir, 'nfs')
            vddk_thumbprint = '11'
            nbdkit_cmd = """
nbdkit -rfv -U - --exportname / \
  --filter=cacheextents --filter=retry vddk server=%s user=%s password=+%s vm=%s \
  file='%s' libdir=%s --run 'qemu-img info $nbd' thumbprint=%s
""" % (vsphere_host, vsphere_user, vsphere_passwd_file, nbdkit_vm_name, nbdkit_file, vddk_libdir, vddk_thumbprint)
            # get thumbprint by a trick
            cmd_result = process.run(
                nbdkit_cmd, shell=True, ignore_status=True)
            output = cmd_result.stdout_text + cmd_result.stderr_text
            vddk_thumbprint = re.search(
                r'PeerThumbprint:\s+(.*)', output).group(1)

            # replace thumbprint with correct value
            nbdkit_cmd = nbdkit_cmd.strip()[:-2] + vddk_thumbprint
            logging.info('nbdkit command:\n%s', nbdkit_cmd)

            # Run the finnal nbdkit command
            output = process.run(nbdkit_cmd, shell=True).stdout_text
            utils_misc.umount(vddk_libdir_src, vddk_libdir, 'nfs')
            if not re.search(r'virtual size', output):
                test.fail('failed to test has_run_againt_vddk7_0')

    def test_memory_max_disk_size():
        """
        check case for bz1913740
        """
        if multiple_versions_compare('[qemu-kvm-5.2.0-3,)'):
            mem_size = "2**63 - 2**30"
            invalid_mem_size = "2**63 - 2**30 + 1"
        else:
            mem_size = "2**63 - 512"
            invalid_mem_size = "2**63 - 512 + 1"

        cmd = 'nbdkit memory $((%s)) --run \'qemu-img info "$uri"\'' % mem_size
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status != 0:
            test.fail('failed to test memory_max_disk_size')

        cmd = 'nbdkit memory $((%s)) --run \'qemu-img info "$uri"\'' % invalid_mem_size
        expected_msg = 'File too large'
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status == 0 or expected_msg in cmd_result.stdout_text:
            test.fail('failed to test memory_max_disk_size')

    if version_requried and not multiple_versions_compare(
            version_requried):
        test.cancel("Testing requries version: %s" % version_requried)

    if checkpoint == 'filter_stats_fd_leak':
        test_filter_stats_fd_leak()
    elif checkpoint == 'has_run_againt_vddk7_0':
        test_has_run_againt_vddk7_0()
    elif checkpoint == 'memory_max_disk_size':
        test_memory_max_disk_size()
    else:
        test.error('Not found testcase: %s' % checkpoint)
