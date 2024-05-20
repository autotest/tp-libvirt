import re
import os
import signal
import logging
import tempfile

from avocado.utils import process
from virttest import data_dir
from virttest import utils_misc
from virttest.utils_conn import build_server_key, build_CA
from virttest.utils_v2v import multiple_versions_compare
from virttest.utils_v2v import params_get
from virttest import utils_v2v
from virttest.utils_conn import update_crypto_policy

LOG = logging.getLogger('avocado.v2v.' + __name__)


def run(test, params, env):
    """
    Basic nbdkit tests
    """
    checkpoint = params.get('checkpoint')
    version_required = params.get('version_required')

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
            LOG.debug('all fds:\n%s', cont)
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
  file='%s' libdir=%s --run 'nbdinfo $uri' thumbprint=%s
""" % (vsphere_host, vsphere_user, vsphere_passwd_file, nbdkit_vm_name, nbdkit_file, vddk_libdir, vddk_thumbprint)
            # get thumbprint by a trick
            cmd_result = process.run(
                nbdkit_cmd, shell=True, ignore_status=True)
            output = cmd_result.stdout_text + cmd_result.stderr_text
            vddk_thumbprint = re.search(
                r'PeerThumbprint:\s+(.*)', output).group(1)

            # replace thumbprint with correct value
            nbdkit_cmd = nbdkit_cmd.strip()[:-2] + vddk_thumbprint
            LOG.info('nbdkit command:\n%s', nbdkit_cmd)

            if checkpoint == 'vddk_stats':
                vddk_stats = params_get(params, "vddk_stats")
                nbdkit_cmd = nbdkit_cmd + ' -D vddk.stats=%s' % vddk_stats
                LOG.info('nbdkit command with -D option:\n%s', nbdkit_cmd)
            if checkpoint == 'backend_datapath_controlpath':
                nbdkit_cmd = nbdkit_cmd + ' -D nbdkit.backend.datapath=0 -D nbdkit.backend.controlpath=0'
                LOG.info('nbdkit command with -D option:\n%s', nbdkit_cmd)
            if checkpoint == 'scan_readahead_blocksize':
                nbdkit_cmd = nbdkit_cmd.replace('--filter=retry', '--filter=scan  --filter=blocksize '
                                                                  '--filter=readahead') + \
                             ' scan-ahead=true scan-clock=true scan-size=2048 scan-forever=true'
                LOG.info('nbdkit command with scan, readahead and blocksize filters:\n%s' % nbdkit_cmd)
            if checkpoint == 'vddk_with_delay_close_open_option':
                nbdkit_cmd = nbdkit_cmd + ' --filter=delay delay-close=40000ms delay-open=40000ms'
                LOG.info('nbdkit command with delay-close and delay-open options:\n%s' % nbdkit_cmd)
            # Run the final nbdkit command
            output = process.run(nbdkit_cmd, shell=True).stdout_text
            utils_misc.umount(vddk_libdir_src, vddk_libdir, 'nfs')
            if checkpoint == 'vddk_stats':
                if vddk_stats == 1 and not re.search(
                        r'VDDK function stats', output):
                    test.fail('failed to test vddk_stats')
                if vddk_stats == 0 and re.search(
                        r'VDDK function stats', output):
                    test.fail('failed to test vddk_stats')
            if checkpoint == 'has_run_againt_vddk7_0' and not re.search(
                    r'export-size', output):
                test.fail('failed to test has_run_againt_vddk7_0')
            if checkpoint == 'backend_datapath_controlpath' and re.search(r'vddk: (open|pread)', output):
                test.fail('fail to test nbdkit.backend.datapath and nbdkit.backend.controlpath option')
            if checkpoint == 'scan_readahead_blocksize' and re.search('error', output):
                test.fail('fail to test scan, readahead and blocksize filters with vddk plugin')
            if checkpoint == 'vddk_with_delay_close_open_option' and re.search(r'nbdkit.*failed', output):
                test.fail('fail to test delay-close and delay-open options with vddk plugin')

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

    def test_data_corruption():
        """
        check case for bz1990134
        """
        cmd = """nbdkit --filter=cow data "33 * 100000" --run 'nbdsh -u $uri -c "h.trim(100000, 0)" ; nbdcopy $uri - | hexdump -C'"""
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if cmd_result.exit_status != 0 or '21 21' in cmd_result.stdout_text:
            test.fail('failed to test data_corruption')

    def test_cve_2019_14850():
        """
        check case for bz1757263
        """
        cred_dict = {
            'cakey': 'ca-key.pem',
            'cacert': 'ca-cert.pem',
            'serverkey': 'server-key.pem',
            'servercert': 'server-cert.pem'}
        tls_dir = data_dir.get_tmp_dir()
        build_CA(tls_dir, credential_dict=cred_dict)
        build_server_key(
            tls_dir,
            credential_dict=cred_dict,
            server_ip='127.0.0.0')
        cmd1 = """echo | nbdkit -fv --tls=require --tls-certificates=/root null --run "nc localhost 10809" --tls-verify-peer"""
        cmd2 = "nbdkit -fv null --run 'sleep 1 >/dev/tcp/localhost/10809' 2>&1"
        for cmd in [cmd1, cmd2]:
            cmd_result = process.run(cmd, shell=True, ignore_status=True)
            if 'open readonly' in cmd_result.stdout_text:
                test.fail('failed to test cve_2019_14850')

    def test_python_error():
        """
        check case for bz1613946
        """
        lines = """
def function():
    raise RuntimeError("error")
def config(key, value):
    function()
def open(readonly):
    raise RuntimeError("open")
def get_size(h):
    raise RuntimeError("get_size")
def pread(h, count, offset):
    raise RuntimeError("pread")
    """

        python_file_path = os.path.join(data_dir.get_tmp_dir(), "python_check.py")
        with open(python_file_path, "w") as f:
            f.write(lines)
        cmd = "nbdkit -fv python %s foo=bar" % python_file_path
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if not re.search('config: error: Traceback', cmd_result.stderr_text):
            test.fail('failed to test enhance_python_error')

    def test_checkwrite_filter():
        lines = """
#!/bin/bash -

set -e

rm -f /tmp/sock

nbdkit --exit-with-parent -U /tmp/sock sh - --filter=checkwrite <<'EOF' &
case "$1" in
  get_size) echo 1048576 ;;
  pread) dd if=/dev/zero count=$3 iflag=count_bytes ;;
  can_extents) exit 0 ;;
  extents)
    echo 0 262144 3
    echo 262144 131072 0
    echo 393216 655360 3
  ;;
  *) exit 2 ;;
esac
EOF

sleep 1

nbdsh -u nbd+unix:///?socket=/tmp/sock -c 'h.zero (655360, 262144, 0)'
        """

        file_path = data_dir.get_tmp_dir()
        shell_file_path = os.path.join(file_path, "bound.sh")
        with open(shell_file_path, "w") as f:
            f.write(lines)
        cmd = "cd %s; chmod +x %s; ./bound.sh" % (file_path, shell_file_path)
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if re.search('core dumped', cmd_result.stderr_text):
            test.fail('failed to test checkwrite filter')

    def test_blocksize_policy_filter():
        cmd_1 = "nbdkit --filter=blocksize-policy memory 1G blocksize-preferred=32K blocksize-maximum=40K " \
                "blocksize-minimum=4K blocksize-error-policy=allow"
        cmd_2 = "nbdinfo nbd://localhost"
        cmd_1_result = process.run(cmd_1, shell=True, ignore_status=True)
        if re.search('error', cmd_1_result.stderr_text):
            test.fail('failed to execute nbdkit command with blocksize-policy filter')
        cmd_2_result = process.run(cmd_2, shell=True, ignore_status=True)
        for data in ['block_size_minimum: 4096', 'block_size_preferred: 32768', 'block_size_maximum: 40960']:
            if not re.search(data, cmd_2_result.stdout_text):
                test.fail('failed to test blocksize policy filter')
        cmd_3 = "ps ax | grep 'nbdkit ' | grep -v grep | awk '{print $1}'"
        cmd_3_result = process.run(cmd_3, shell=True, ignore_status=True).stdout_text.split()
        os.kill(int(cmd_3_result[0]), signal.SIGKILL)

    def test_curl_multi_conn():
        cmd = "nbdkit -r curl file:/var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2" \
              " --run 'nbdinfo $uri' | grep can_multi_conn"
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if re.search('can_multi_conn: false', cmd_result.stderr_text):
            test.fail('curl plugin does not support multi_conn')

    def test_luks_filter():
        process.run("qemu-img create -f luks --object secret,data=LETMEPASS,id=sec0 -o key-secret=sec0 "
                    "encrypted.img 100M", shell=True)
        cmd_2 = process.run("nbdkit file encrypted.img --filter=luks passphrase=LETMEPASS "
                            "--run 'nbdcopy $nbd data.img'", shell=True, ignore_status=True)
        cmd_3 = process.run("nbdkit file data.img --filter=luks passphrase=LETMEPASS --run 'nbdcopy $nbd data-b.img'",
                            shell=True, ignore_status=True)
        if re.search('error', cmd_2.stderr_text):
            test.fail('failed to use luks filter to read luks image which is created by qemu-img')
        if re.search('nbdkit command was killed by signal 11', cmd_3.stderr_text):
            test.fail('nbdkit was killed by signal 11')

    def test_plugin_file_fd_fddir_option():
        # set file descriptor < = 2
        cmd_fd_1 = "nbdkit file fd=2  --run 'nbdinfo $uri'"
        cmd_fddir_1 = 'nbdkit file dirfd=2'
        # set a nonexistent file descriptor
        cmd_fd_2 = "nbdkit file fd=7  --run 'nbdinfo $uri'"
        cmd_fddir_2 = 'nbdkit file dirfd=7'
        # Set a valid file descriptor for fd
        cmd_fd_3 = "exec 6<> /tmp/hello ; echo hello >& 6 ; nbdkit file fd=6 --run 'nbdinfo $uri'"
        # Set a valid file descriptor for fddir # pylint: disable=C0401
        cmd_fddir_3 = "mkdir -p /tmp/fddir ; exec 6< /tmp/fddir; nbdkit file dirfd=6 --run 'nbdinfo --list $uri'"
        # set fd and dirfd at the same time in command line # pylint: disable=C0401
        cmd_fd_fddir = "nbdkit file fd=7 dirfd=6"
        cmd_fd_1_r = process.run(cmd_fd_1, shell=True, ignore_status=True)
        cmd_fddir_1_r = process.run(cmd_fddir_1, shell=True, ignore_status=True)
        cmd_fd_2_r = process.run(cmd_fd_2, shell=True, ignore_status=True)
        cmd_fddir_2_r = process.run(cmd_fddir_2, shell=True, ignore_status=True)
        cmd_fd_3_r = process.run(cmd_fd_3, shell=True, ignore_status=True)
        cmd_fddir_3_r = process.run(cmd_fddir_3, shell=True, ignore_status=True)
        cmd_fd_fddir_r = process.run(cmd_fd_fddir, shell=True, ignore_status=True)
        for result in [cmd_fd_1_r, cmd_fddir_1_r]:
            if not re.search(r'file descriptor must be > 2', result.stderr_text):
                test.fail('got unexpected result when set file descriptor <= 2 for fd and fddir option')
        if not re.search(r'fd is not regular or block device', cmd_fd_2_r.stderr_text):
            test.fail('got unexpected result when set a nonexistent file descriptorfor for fd option')
        if not re.search(r'dirfd is not a directory', cmd_fddir_2_r.stderr_text):
            test.fail('got unexpected result when set a nonexistent file descriptorfor for fddir option')
        for result in [cmd_fd_3_r, cmd_fddir_3_r]:
            if re.search('error', result.stdout_text):
                test.fail('got unexpected result when set a valid file descriptor for fd and fddir option')
        if not re.search(r'file|dir|fd|dirfd parameter can only appear once on the command line',
                         cmd_fd_fddir_r.stderr_text):
            test.fail('got unexpected result when set fd and dirfd at the same time in command line')

    def check_assertion_failure():
        cmd = 'nbdcopy -- [ nbdkit --exit-with-parent -v --filter=error pattern 5M error-pread-rate=0.5 ] null:'
        cmd_result = process.run(cmd, shell=True, ignore_status=True)
        if re.search(r'Assertion\s+\w+failed', cmd_result.stdout_text):
            test.fail('nbdkit server crash with assertion failure')

    def check_vddk_filters_thread_model():
        filters_list = params.get('filters')
        for filter in list(filters_list.split(' ')):
            cmd_1 = "nbdkit vddk --filter=%s --dump-plugin | grep thread" % filter
            cmd_2 = "nbdkit vddk --dump-plugin | grep thread"
            for cmd in [cmd_1, cmd_2]:
                cmd_result = process.run(cmd, shell=True, ignore_status=True)
                if len(re.findall('parallel', cmd_result.stdout_text)) != 2:
                    test.fail('thread mode of %s is not parallel in vddk plugin' % filter)

    def check_vddk_create_options():
        create_types = params.get('create_types')
        create_adapter_types = params.get('create_adapter_types')
        create_hwversions = params.get('create_hwversions')
        vddk_libdir_src = params_get(params, "vddk_libdir_src")
        with tempfile.TemporaryDirectory(prefix='vddklib_') as vddk_libdir:
            utils_misc.mount(vddk_libdir_src, vddk_libdir, 'nfs')
            for create_type in list(create_types.split(' ')):
                for create_adapter_type in list(create_adapter_types.split(' ')):
                    for create_hwversion in list(create_hwversions.split(' ')):
                        tmp_path = data_dir.get_tmp_dir()
                        disk_path = os.path.join(tmp_path, 'vddk-create-options.vmdk')
                        cmd = "nbdkit vddk file='%s' create=true create-type=%s " \
                              "create-adapter-type=%s create-hwversion=%s create-size=100M libdir=%s --run " \
                              "'nbdinfo $uri'; rm -rf %s/*" % (disk_path, create_type, create_adapter_type,
                                                               create_hwversion, vddk_libdir, tmp_path)
                        cmd_result = process.run(cmd, shell=True, ignore_status=True)
                        if re.search('error', cmd_result.stdout_text) or re.search('error', cmd_result.stderr_text):
                            test.fail('fail to create vmdk with vddk create option %s, %s, %s' %
                                      (create_type, create_adapter_type, create_hwversion))
            utils_misc.umount(vddk_libdir_src, vddk_libdir, 'nfs')

    def annocheck_test_nbdkit():
        tmp_path = data_dir.get_tmp_dir()
        process.run('yum download nbdkit-server nbdkit-server-debuginfo --destdir=%s' % tmp_path, shell=True,
                    ignore_status=True)
        cmd_3 = 'annocheck -v --skip-cf-protection --skip-glibcxx-assertions --skip-glibcxx-assertions ' \
                '--skip-stack-realign --section-size=.gnu.build.attributes --ignore-gaps ' \
                '%s/%s --debug-rpm=%s/%s' % (tmp_path, (process.run('ls %s/nbdkit-server-1*' % tmp_path,
                                                                    shell=True, ignore_status=True).
                                                        stdout_text.split('/'))[-1].strip('\n'), tmp_path,
                                             (process.run('ls %s/nbdkit-server-debuginfo*' % tmp_path,  shell=True,
                                                          ignore_status=True).stdout_text.split('/'))[-1].strip('\n'))
        cmd_3_result = process.run(cmd_3, shell=True, ignore_status=True)
        if re.search('FAIL', cmd_3_result.stdout_text) and len(cmd_3_result.stdout_text) == 0:
            test.fail('fail to test ndbkit-server rpm package with annocheck tool')

    def statsfile_option():
        tmp_path = data_dir.get_tmp_dir()
        process.run('nbdkit --filter=exitlast --filter=stats memory 2G statsfile=%s/example.txt' % tmp_path,
                    shell=True, ignore_status=True)
        process.run('qemu-img create -f qcow2 data.img 2G ; nbdcopy data.img nbd://localhost', shell=True,
                    ignore_status=True)
        cmd_3 = process.run('cat %s/example.txt' % tmp_path, shell=True, ignore_status=True)
        if not re.search('Request size and alignment breakdown', cmd_3.stdout_text):
            test.fail('fail to test statfile option')

    def test_rate_filter():
        cmd = process.run("nbdkit --filter=rate memory 64M rate=1M connection-rate=500K burstiness=20 "
                          "--run 'nbdinfo $uri'", shell=True, ignore_status=True)
        if re.search('error', cmd.stdout_text):
            test.fail('fail to test rate filter')

    def enable_legacy_cryptography(hostname):
        """
        Enable the legacy sha1 algorithm.
        """
        ssh_config = ("Host %s\n"
                      "  KexAlgorithms            +diffie-hellman-group14-sha1\n"
                      "  MACs                     +hmac-sha1\n"
                      "  HostKeyAlgorithms        +ssh-rsa\n"
                      "  PubkeyAcceptedKeyTypes   +ssh-rsa\n"
                      "  PubkeyAcceptedAlgorithms +ssh-rsa") % hostname

        openssl_cnf = (".include /etc/ssl/openssl.cnf\n"
                       "[openssl_init]\n"
                       "alg_section = evp_properties\n"
                       "[evp_properties]\n"
                       "rh-allow-sha1-signatures = yes")

        with open(os.path.expanduser('~/.ssh/config'), 'w') as fd:
            fd.write(ssh_config)

        with open(os.path.expanduser('~/openssl-sha1.cnf'), 'w') as fd:
            fd.write(openssl_cnf)

        # export the environment variable
        os.environ['OPENSSL_CONF'] = os.path.expanduser('~/openssl-sha1.cnf')
        LOG.debug('OPENSSL_CONF is %s' % os.getenv('OPENSSL_CONF'))

    def test_ssh_create_option():
        xen_host_user = params_get(params, "xen_host_user")
        xen_host_passwd = params_get(params, "xen_host_passwd")
        xen_host = params_get(params, "xen_host")
        # Setup ssh-agent access to xen hypervisor
        support_ver = '[virt-v2v-2.0.7-4,)'
        if utils_v2v.multiple_versions_compare(support_ver):
            enable_legacy_cryptography(xen_host)
        else:
            update_crypto_policy("LEGACY")
        LOG.info('set up ssh-agent access ')
        xen_pubkey, xen_session = utils_v2v.v2v_setup_ssh_key(
            xen_host, xen_host_user, xen_host_passwd, auto_close=False)
        utils_misc.add_identities_into_ssh_agent()
        cmd = process.run("nbdkit ssh host=%s /tmp/disk.img user=%s password=%s create=true "
                          "create-mode=0644 create-size=10M --run 'nbdinfo --can connect $uri'" %
                          (xen_host, xen_host_user, xen_host_passwd), shell=True)
        if re.search('error', (cmd.stdout_text + cmd.stderr_text)):
            test.fail('fail to test create options of ssh plugin')
        utils_v2v.v2v_setup_ssh_key_cleanup(xen_session, xen_pubkey)
        process.run('ssh-agent -k')

    def delay_close_delay_open_options():
        #Check options when clients use NBD_CMD_DISC (libnbd nbd_shutdown) or clients which drop the connection
        nbdsh_s = 'time nbdsh -u $uri -c "h.shutdown()"'
        cmd_down = process.run("nbdkit --filter=delay null delay-close=3 --run '%s'" % nbdsh_s,
                               shell=True, ignore_status=True)
        #Check options when clients do not use NBD_CMD_DISC (libnbd nbd_shutdown)
        nbdsh_p = 'time nbdsh -u $uri -c "pass"'
        cmd_pass = process.run("nbdkit --filter=delay null delay-close=3 --run '%s'" % nbdsh_p,
                               shell=True, ignore_status=True)
        if not re.search('0m3', cmd_down.stderr_text):
            test.fail('fail to test delay-close option when nbdkit clients shutdown')
        if not re.search('0m0', cmd_pass.stderr_text):
            test.fail('fail to test delay-close option when nbdkit clients are not shutdown')
        #Set invalid number for delay option
        values = ['10secs', '40SECS', '10s', '10MS', '1:']
        for value in values:
            cmd_num = process.run("nbdkit null --filter=delay delay-open=%s --run 'nbdinfo $uri'" % value,
                                  shell=True, ignore_status=True)
            if not re.search('could not parse number', cmd_num.stderr_text):
                test.fail('get unexpected result when set invalid value for nbdkit delay options)')
        #Check error when nbdkit aborts early
        cmd_aborts = process.run("nbdkit --filter=delay null delay-close=3 --run 'nbdinfo --size $uri; "
                                 "nbdinfo --size $uri'", shell=True)
        if re.search('error', cmd_aborts.stdout_text):
            test.fail('get unexpected error when test delay option and nbdkit aborts early')

    def cow_on_read_true():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        process.run('nbdkit file %s --filter=cow cow-on-read=true' % image_path, shell=True)
        cmd_proc = process.run('ls -l /proc/`pidof nbdkit`/fd', shell=True)
        if re.search(r'3 -> /var/tmp/.*(deleted)', cmd_proc.stdout_text):
            output_1 = process.run("stat -L --format='%b %B %o' /proc/`pidof nbdkit`/fd/3", shell=True)
            if re.search(r"0 512 4096", output_1.stdout_text):
                process.run("nbdsh -u nbd://localhost -c 'h.pread(8*1024*1024, 0)'", shell=True)
                output_2 = process.run("stat -L --format='%b %B %o' /proc/`pidof nbdkit`/fd/3", shell=True)
                if not re.search(r"16384 512 4096", output_2.stdout_text):
                    test.fail('cow-on-read=true option does not work')
        else:
            test.fail('cannot find nbdkit fd process when test cow_on_read=true option')
        cmd_kill = "ps ax | grep 'nbdkit ' | grep -v grep | awk '{print $1}'"
        cmd_kill_result = process.run(cmd_kill, shell=True, ignore_status=True).stdout_text.split()
        os.kill(int(cmd_kill_result[0]), signal.SIGKILL)

    def cow_on_read_path():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        cmd_inspect = 'time virt-inspector --format=raw -a "$uri"'
        time_1 = process.run("nbdkit file %s --filter=cow --filter=delay rdelay=200ms cow-on-read=%s "
                             "--run '%s' > %s/time1.log" % (image_path, tmp_path, cmd_inspect, tmp_path),
                             shell=True, ignore_status=True)
        time_2 = process.run("nbdkit file %s --filter=cow --filter=delay rdelay=200ms --run '%s' > %s/time2.log"
                             % (image_path, cmd_inspect, tmp_path), shell=True, ignore_status=True)
        if not (int(''.join(filter(str.isdigit, re.search(r'real.*m', time_1.stderr_text).group(0)))) <
                int(''.join(filter(str.isdigit, re.search(r'real.*m', time_2.stderr_text).group(0))))):
            test.fail('fail to test cow-on-read=/path option')

    def cow_block_size():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        cmd_inspect = 'virt-inspector --format=raw -a "$uri"'
        output_1 = process.run("nbdkit file %s --filter=cow --filter=delay rdelay=200ms cow-block-size=4096 "
                               "--run '%s'" % (image_path, cmd_inspect), shell=True, ignore_status=True)
        output_2 = process.run("nbdkit file %s --filter=cow --filter=delay rdelay=200ms cow-block-size=4K "
                               "--run '%s'" % (image_path, cmd_inspect), shell=True, ignore_status=True)
        for output in [output_1.stderr_text, output_2.stderr_text]:
            if re.search('nbdkit: error: cow-block-size is out of range.*not a power of 2', output):
                test.fail('fail to test cow-block-size option')

    def reduce_verbosity_debugging():
        cmd_nbdsh = 'nbdsh -u $uri -c "h.pwrite(bytearray(1024), 0)"'
        cmd = process.run("nbdkit -fv --filter=cow memory 10k --run '%s' |& grep 'debug: cow: blk'" % cmd_nbdsh,
                          shell=True, ignore_status=True)
        output = cmd.stdout_text + cmd.stderr_text
        if len(output) != 0:
            test.fail('nbdkit does not reduce verbosity of debugging')

    def cache_on_read():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        cmd_inspect = 'time virt-inspector --format=raw -a "$uri"'
        time_1 = process.run("nbdkit file %s --filter=cache --filter=delay rdelay=200ms cache-on-read=true "
                             "--run '%s' > %s/time1.log" % (image_path, cmd_inspect, tmp_path),
                             shell=True, ignore_status=True)
        time_2 = process.run("nbdkit file %s --filter=cache --filter=delay rdelay=200ms "
                             "cache-on-read=%s --run '%s' > %s/time2.log" %
                             (image_path, tmp_path, cmd_inspect, tmp_path), shell=True, ignore_status=True)
        time_3 = process.run("nbdkit file %s --filter=cache --filter=delay rdelay=200ms --run '%s' > %s/time3.log"
                             % (image_path, cmd_inspect, tmp_path), shell=True, ignore_status=True)
        for time in [int(''.join(filter(str.isdigit, re.search(r'real.*m', time_1.stderr_text).group(0)))),
                     int(''.join(filter(str.isdigit, re.search(r'real.*m', time_2.stderr_text).group(0))))]:
            if time > int(''.join(filter(str.isdigit, re.search(r'real.*m', time_3.stderr_text).group(0)))):
                test.fail('fail to test cache-on-read option')

    def cache_min_block_size():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        cmd_inspect = 'time virt-inspector --format=raw -a "$uri"'
        output_1 = process.run("nbdkit file %s --filter=cache --filter=delay rdelay=200ms cache-on-read=true "
                               "cache-min-block-size=4k --run '%s' > %s/time1.log" %
                               (image_path, cmd_inspect, tmp_path), shell=True, ignore_status=True)
        output_2 = process.run("nbdkit file %s --filter=cache --filter=delay rdelay=200ms cache-on-read=true "
                               "cache-min-block-size=64K --run '%s' > %s/time2.log" %
                               (image_path, cmd_inspect, tmp_path), shell=True, ignore_status=True)
        for output in [output_1.stderr_text, output_2.stderr_text]:
            if re.search('nbdkit: error: cache-min-block-size.*is too small or too large', output):
                test.fail('fail to test cache-min-block-size option')

    def cve_starttls():
        tmp_path = data_dir.get_tmp_dir()
        process.run("yum install libtool 'dnf-command(download)' -y", shell=True, ignore_status=True)
        process.run('yum download --source nbdkit --destdir=%s' % tmp_path, shell=True,
                    ignore_status=True)
        process.run('rm -rf /etc/yum.repos.d/rhel9-appsource.repo', shell=True, ignore_status=True)
        process.run('cd %s ; rpmbuild -rp %s' % (tmp_path, (process.run('ls %s/nbdkit*.src.rpm' % tmp_path, shell=True).
                                                            stdout_text.split('/'))[-1].strip('\n')), shell=True)
        check_file = process.run('ls /root/rpmbuild/BUILD/nbdkit-*/server/protocol-handshake-newstyle.c',
                                 shell=True).stdout_text.strip('\n')
        count = 0
        with open(check_file, "r") as ff:
            lines = ff.readlines()
            for line in lines:
                if line.strip() == 'free (conn->exportname_from_set_meta_context);':
                    count += 1
        if count == 0:
            test.fail('fail to test nbdkit cve starttls')

    def test_protect_filter():
        from subprocess import Popen, PIPE, STDOUT
        protect_data = '"AB" * 32768'
        p1 = Popen("nbdkit -f --filter=protect data '%s' protect=0-1023 --run 'nbdsh -u $uri'" %
                   protect_data, shell=True, stdout=PIPE, stdin=PIPE, stderr=STDOUT)

        grep_stdout_1 = p1.communicate(input=b"buf=b'01'*256\nh.pwrite(buf,32768)\nprint(h.pread(512,32768))\n")[0]
        if re.search('AB', grep_stdout_1.decode()):
            test.fail('the data is incorrect when write data with protect filter')
        p2 = Popen("nbdkit -f --filter=protect data '%s' protect=0-1023 --run 'nbdsh -u $uri'" %
                   protect_data, shell=True, stdout=PIPE, stdin=PIPE, stderr=STDOUT)

        grep_stdout_2 = p2.communicate(input=b"buf=b'01'*256\nh.pwrite(buf,32768)\nprint(h.pread(512,32768))\n"
                                             b"h.pwrite(buf,0)\n")[0]
        if not re.search('Operation not permitted ', grep_stdout_2.decode()):
            test.fail('fail to test protect filter')

    def security_label():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        label = process.run("ps -Z", shell=True, ignore_status=True)
        LOG.info("label %s", label)
        sec_label = re.search('unconfined_u:unconfined_r:unconfined.*\n', label.stdout_text).group(0).split(' ')[0]
        LOG.info("seclabel %s", sec_label)
        test_co_label = process.run("nbdkit --filter=ip file %s allow=security:%s deny=all --run 'nbdinfo $uri'" %
                                    (image_path, sec_label), shell=True, ignore_status=True)
        test_inco_label = process.run("nbdkit --filter=ip file %s allow=security:%s1 deny=all --run 'nbdinfo $uri'" %
                                      (image_path, sec_label), shell=True, ignore_status=True)
        if re.search(" error", test_co_label.stdout_text) or not re.search("error: client not permitted",
                                                                           test_inco_label.stderr_text):
            test.fail('fail to test security label of IP filter')

    def partition_sectorsize():
        sector_size = params_get(params, "sector_size")
        guest_images = params_get(params, "guest_images")
        with tempfile.TemporaryDirectory(prefix='guestimages_') as images_dir:
            utils_misc.mount(guest_images, images_dir, 'nfs')
            img_dir = data_dir.get_tmp_dir()
            process.run('cp -R %s/* %s' % (images_dir, img_dir), shell=True, ignore_status=True)
            utils_misc.umount(guest_images, images_dir, 'nfs')
        image_list = process.run('ls %s' % img_dir, shell=True).stdout_text.strip('env').split('\n')[1:-1]
        for image in image_list:
            for size in list(sector_size.split(' ')):
                cmd = process.run("nbdkit --filter=partition file %s/%s partition=1 partition-sectorsize=%s --run "
                                  "'nbdinfo $uri'" % (img_dir, image, size), shell=True, ignore_status=True)
                if 'non-efi' not in image and '512' in image and size == '4k' and \
                        not re.search('.*try using partition-sectorsize=512', cmd.stderr_text):
                    test.fail('fail to test 512 image and partition-sectorsize=4k')
                if '4k' in image and size == '512' and \
                        not re.search('.*try using partition-sectorsize=4k', cmd.stderr_text):
                    test.fail('fail to test 4k image and partition-sectorsize=512')
                elif re.search('nbdkit.*error', cmd.stdout_text):
                    test.fail('fail to test partition-sectorsize')

    def ones_byte():
        byte_size = params_get(params, "byte_size")
        for size in list(byte_size.split(' ')):
            cmd = process.run("nbdkit ones size=4M byte=%s  --run 'nbdinfo $uri'" % size, shell=True, ignore_status=True)
            if size == '1' and not re.search(r'.*\\001\\001', cmd.stdout_text):
                test.fail('fail to test ones plugin with byte=1')
            if size == 'oxff' and not re.search('ISO-8859 text', cmd.stdout_text):
                test.fail('fail to test ones plugin with byte=oxff')
            if size == '256' and not re.search('could not parse number', cmd.stderr_text):
                test.fail('fail to test ones plugin with byte=256')

    def test_evil_filter():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        option_evil = params_get(params, "option_evil")
        option_evil_probability = params_get(params, "option_evil_probability")
        option_evil_seed = params_get(params, "option_evil_seed")
        option_evil_stuck_probability = params_get(params, "option_evil_stuck_probability")
        for mode in list(option_evil.split(' ')):
            for probability in list(option_evil_probability.split(' ')):
                for seed in list(option_evil_seed.split(' ')):
                    for stuck_probability in list(option_evil_stuck_probability.split(' ')):
                        cmd = process.run("nbdkit --filter=evil file %s evil=%s evil-probability=%s evil-seed=%s "
                                          "evil-stuck-probability=%s --run 'nbdcopy -p $uri null:'" %
                                          (image_path, mode, probability, seed, stuck_probability),
                                          shell=True, ignore_status=True)
                        output = cmd.stdout_text + cmd.stderr_text
                        if re.search('nbdkit.*error', output):
                            test.fail('fail to test evil filter')

    def test_tar_filter():
        tmp_path = data_dir.get_tmp_dir()
        image_path = os.path.join(tmp_path, 'latest-rhel9.img')
        process.run('qemu-img convert -f qcow2 -O raw /var/lib/avocado/data/avocado-vt/images/jeos-27-x86_64.qcow2'
                    ' %s' % image_path, shell=True)
        process.run('cd %s ; tar cvf latest-rhel9-image.tar latest-rhel9.img' % tmp_path, shell=True)
        option_tar_limit = params_get(params, "option_tar_limit")
        option_tar_entry = params_get(params, "option_tar_entry")
        for limit in list(option_tar_limit.split(' ')):
            for entry in list(option_tar_entry.split(' ')):
                cmd = process.run(" nbdkit file %s/latest-rhel9-image.tar --filter=tar tar-entry=%s tar-limit=%s "
                                  "--run 'nbdcopy -p $uri null:'" % (tmp_path, entry, limit), shell=True, ignore_status=True)
                output = cmd.stdout_text + cmd.stderr_text
                if entry == 'latest-rhel9.img' and re.search('nbdkit.*error', output):
                    test.fail('fail to test tar filter')
                elif entry != 'latest-rhel9.img' and not re.search('nbdkit.*error', output):
                    test.fail('fail to test tar filter')

    def check_curl_time_option():
        image_url = params_get(params, 'external_image_url')
        cmd = process.run("nbdkit -rvf -U - curl %s -D curl.times=1 -D curl.verbose=1 -D curl.verbose.ids=1 "
                          "--run 'nbdcopy -p $uri null:'" % image_url, shell=True, ignore_status=True)
        if not re.search(r'nbdkit: debug: times .*-D curl.times=1.*', cmd.stderr_text):
            test.fail('fail to test curl.time option')

    if version_required and not multiple_versions_compare(
            version_required):
        test.cancel("Testing requires version: %s" % version_required)

    if checkpoint == 'filter_stats_fd_leak':
        test_filter_stats_fd_leak()
    elif checkpoint in ['has_run_againt_vddk7_0', 'vddk_stats', 'backend_datapath_controlpath',
                        'scan_readahead_blocksize', 'vddk_with_delay_close_open_option']:
        test_has_run_againt_vddk7_0()
    elif checkpoint == 'memory_max_disk_size':
        test_memory_max_disk_size()
    elif checkpoint == 'data_corruption':
        test_data_corruption()
    elif checkpoint == 'cve_2019_14850':
        test_cve_2019_14850()
    elif checkpoint == 'enhance_python_error':
        test_python_error()
    elif checkpoint == 'checkwrite':
        test_checkwrite_filter()
    elif checkpoint == 'blocksize_policy':
        test_blocksize_policy_filter()
    elif checkpoint == 'test_curl_multi_conn':
        test_curl_multi_conn()
    elif checkpoint == 'test_luks_filter':
        test_luks_filter()
    elif checkpoint == 'plugin_file_fd_fddir_option':
        test_plugin_file_fd_fddir_option()
    elif checkpoint == 'check_assertion_failure':
        check_assertion_failure()
    elif checkpoint == 'check_vddk_filters_thread_model':
        check_vddk_filters_thread_model()
    elif checkpoint == 'check_vddk_create_options':
        check_vddk_create_options()
    elif checkpoint == 'annocheck_test_nbdkit':
        annocheck_test_nbdkit()
    elif checkpoint == 'statsfile_option':
        statsfile_option()
    elif checkpoint == 'test_rate_filter':
        test_rate_filter()
    elif checkpoint == 'test_ssh_create_option':
        test_ssh_create_option()
    elif checkpoint == 'delay_close_delay_open_options':
        delay_close_delay_open_options()
    elif checkpoint == 'cow_on_read_true':
        cow_on_read_true()
    elif checkpoint == 'cow_on_read_path':
        cow_on_read_path()
    elif checkpoint == 'cow_block_size':
        cow_block_size()
    elif checkpoint == 'reduce_verbosity_debugging':
        reduce_verbosity_debugging()
    elif checkpoint == 'cache_on_read':
        cache_on_read()
    elif checkpoint == 'cache_min_block_size':
        cache_min_block_size()
    elif checkpoint == 'cve_starttls':
        cve_starttls()
    elif checkpoint == 'test_protect_filter':
        test_protect_filter()
    elif checkpoint == 'security_label':
        security_label()
    elif checkpoint == 'partition_sectorsize':
        partition_sectorsize()
    elif checkpoint == 'ones_byte':
        ones_byte()
    elif checkpoint == 'test_evil_filter':
        test_evil_filter()
    elif checkpoint == 'test_tar_filter':
        test_tar_filter()
    elif checkpoint == 'check_curl_time_option':
        check_curl_time_option()
    else:
        test.error('Not found testcase: %s' % checkpoint)
