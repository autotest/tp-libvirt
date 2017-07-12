import re
import os
import ast
import shutil
import logging
from avocado.utils import process
from autotest.client import utils
from virttest import virt_vm, virsh
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import rng
from provider import libvirt_version


def run(test, params, env):
    """
    Test rng device options.

    1.Prepare test environment, destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def modify_rng_xml(dparams, sync=True):
        """
        Modify interface xml options
        """
        rng_model = dparams.get("rng_model", "virtio")
        rng_rate = dparams.get("rng_rate")
        backend_model = dparams.get("backend_model", "random")
        backend_type = dparams.get("backend_type")
        backend_dev = dparams.get("backend_dev", "")
        backend_source_list = dparams.get("backend_source",
                                          "").split()
        backend_protocol = dparams.get("backend_protocol")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        rng_xml = rng.Rng()
        rng_xml.rng_model = rng_model
        if rng_rate:
            rng_xml.rate = ast.literal_eval(rng_rate)
        backend = rng.Rng.Backend()
        backend.backend_model = backend_model
        if backend_type:
            backend.backend_type = backend_type
        if backend_dev:
            backend.backend_dev = backend_dev
        if backend_source_list:
            source_list = [ast.literal_eval(source) for source in
                           backend_source_list]
            backend.source = source_list
        if backend_protocol:
            backend.backend_protocol = backend_protocol
        rng_xml.backend = backend

        logging.debug("Rng xml: %s", rng_xml)
        if sync:
            vmxml.add_device(rng_xml)
            vmxml.xmltreefile.write()
            vmxml.sync()
        else:
            status = libvirt.exec_virsh_edit(
                vm_name, [(r":/<devices>/s/$/%s" %
                           re.findall(r"<rng.*<\/rng>",
                                      str(rng_xml), re.M
                                      )[0].replace("/", "\/"))])
            if not status:
                test.fail("Failed to edit vm xml")

    def check_qemu_cmd(dparams):
        """
        Verify qemu-kvm command line.
        """
        rng_model = dparams.get("rng_model", "virtio")
        rng_rate = dparams.get("rng_rate")
        backend_type = dparams.get("backend_type")
        backend_source_list = dparams.get("backend_source",
                                          "").split()
        cmd = ("ps -ef | grep %s | grep -v grep" % vm_name)
        chardev = src_host = src_port = None
        if backend_type == "tcp":
            chardev = "socket"
        elif backend_type == "udp":
            chardev = "udp"
        for bc_source in backend_source_list:
            source = ast.literal_eval(bc_source)
            if "mode" in source and source['mode'] == "connect":
                src_host = source['host']
                src_port = source['service']

        if chardev and src_host and src_port:
            cmd += (" | grep 'chardev %s,.*host=%s,port=%s'"
                    % (chardev, src_host, src_port))
        if rng_model == "virtio":
            cmd += (" | grep 'device virtio-rng-pci'")
        if rng_rate:
            rate = ast.literal_eval(rng_rate)
            cmd += (" | grep 'max-bytes=%s,period=%s'"
                    % (rate['bytes'], rate['period']))
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            test.fail("Cann't see rng option"
                      " in command line")

    def check_host():
        """
        Check random device on host
        """
        backend_dev = params.get("backend_dev")
        if backend_dev:
            cmd = "lsof |grep %s" % backend_dev
            ret = process.run(cmd, ignore_status=True, shell=True)
            if ret.exit_status or not ret.stdout.count("qemu"):
                test.fail("Failed to check random device"
                          " on host, command output: %s" %
                          ret.stdout)

    def check_snapshot(bgjob=None):
        """
        Do snapshot operation and check the results
        """
        snapshot_name1 = "snap.s1"
        snapshot_name2 = "snap.s2"
        if not snapshot_vm_running:
            vm.destroy(gracefully=False)
        ret = virsh.snapshot_create_as(vm_name, snapshot_name1)
        libvirt.check_exit_status(ret)
        snap_lists = virsh.snapshot_list(vm_name)
        if snapshot_name not in snap_lists:
            test.fail("Snapshot %s doesn't exist"
                      % snapshot_name)

        if snapshot_vm_running:
            options = "--force"
        else:
            options = ""
        ret = virsh.snapshot_revert(
            vm_name, ("%s %s" % (snapshot_name, options)))
        libvirt.check_exit_status(ret)
        ret = virsh.dumpxml(vm_name)
        if ret.stdout.count("<rng model="):
            test.fail("Found rng device in xml")

        if snapshot_with_rng:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            if bgjob:
                bgjob.kill_func()
            modify_rng_xml(params, False)

        # Start the domain before disk-only snapshot
        if vm.is_dead():
            # Add random server
            if params.get("backend_type") == "tcp":
                cmd = "cat /dev/random | nc -4 -l localhost 1024"
                bgjob = utils.AsyncJob(cmd)
            vm.start()
            vm.wait_for_login().close()
        err_msgs = ("live disk snapshot not supported"
                    " with this QEMU binary")
        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --disk-only"
                                       % snapshot_name2)
        if ret.exit_status:
            if ret.stderr.count(err_msgs):
                test.skip(err_msgs)
            else:
                test.fail("Failed to create external snapshot")
        snap_lists = virsh.snapshot_list(vm_name)
        if snapshot_name2 not in snap_lists:
            test.fail("Failed to check snapshot list")

        ret = virsh.domblklist(vm_name)
        if not ret.stdout.count(snapshot_name2):
            test.fail("Failed to find snapshot disk")

    def check_guest(session):
        """
        Check random device on guest
        """
        rng_files = (
            "/sys/devices/virtual/misc/hw_random/rng_available",
            "/sys/devices/virtual/misc/hw_random/rng_current")
        rng_avail = session.cmd_output("cat %s" % rng_files[0],
                                       timeout=600).strip()
        rng_currt = session.cmd_output("cat %s" % rng_files[1],
                                       timeout=600).strip()
        logging.debug("rng avail:%s, current:%s", rng_avail, rng_currt)
        if not rng_currt.count("virtio") or rng_currt not in rng_avail:
            test.fail("Failed to check rng file on guest")

        # Read the random device
        cmd = ("dd if=/dev/hwrng of=rng.test count=100"
               " && rm -f rng.test")
        ret, output = session.cmd_status_output(cmd, timeout=600)
        if ret:
            test.fail("Failed to read the random device")
        rng_rate = params.get("rng_rate")
        if rng_rate:
            rate_bytes, rate_period = ast.literal_eval(rng_rate).values()
            rate_conf = float(rate_bytes) / (float(rate_period)/1000)
            ret = re.search(r"(\d+) bytes.*copied, (\d+.\d+) s",
                            output, re.M)
            if not ret:
                test.fail("Can't find rate from output")
            rate_real = float(ret.group(1)) / float(ret.group(2))
            logging.debug("Find rate: %s, config rate: %s",
                          rate_real, rate_conf)
            if rate_real > rate_conf * 1.2:
                test.fail("The rate of reading exceed"
                          " the limitation of configuration")
        if device_num > 1:
            rng_dev = rng_avail.split()
            if len(rng_dev) != device_num:
                test.skip("Multiple virtio-rng devices are not"
                          " supported on this guest kernel. "
                          "Bug: https://bugzilla.redhat.com/"
                          "show_bug.cgi?id=915335")
            session.cmd("echo -n %s > %s" % (rng_dev[1], rng_files[1]))
            # Read the random device
            if session.cmd_status(cmd, timeout=120):
                test.fail("Failed to read the random device")

    start_error = "yes" == params.get("start_error", "no")

    test_host = "yes" == params.get("test_host", "no")
    test_guest = "yes" == params.get("test_guest", "no")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    test_snapshot = "yes" == params.get("test_snapshot", "no")
    snapshot_vm_running = "yes" == params.get("snapshot_vm_running",
                                              "no")
    snapshot_with_rng = "yes" == params.get("snapshot_with_rng", "no")
    snapshot_name = params.get("snapshot_name")
    device_num = int(params.get("device_num", 1))

    if device_num > 1 and not libvirt_version.version_compare(1, 2, 7):
        test.skip("Multiple virtio-rng devices not "
                  "supported on this libvirt version")
    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Try to install rng-tools on host, it can speed up random rate
    # if installation failed, ignore the error and continue the test
    if utils_package.package_install(["rng-tools"]):
        rngd_conf = "/etc/sysconfig/rngd"
        rngd_srv = "/usr/lib/systemd/system/rngd.service"
        if os.path.exists(rngd_conf):
            # For rhel6 host, add extraoptions
            with open(rngd_conf, 'w') as f_rng:
                f_rng.write('EXTRAOPTIONS="--rng-device /dev/urandom"')
        elif os.path.exists(rngd_srv):
            # For rhel7 host, modify start options
            rngd_srv_conf = "/etc/systemd/system/rngd.service"
            if not os.path.exists(rngd_srv_conf):
                shutil.copy(rngd_srv, rngd_srv_conf)
            process.run("sed -i -e 's#^ExecStart=.*#ExecStart=/sbin/rngd"
                        " -f -r /dev/urandom -o /dev/random#' %s"
                        % rngd_srv_conf, shell=True)
            process.run('systemctl daemon-reload')
        process.run("service rngd start")

    # Build the xml and run test.
    try:
        bgjob = None
        # Take snapshot if needed
        if snapshot_name:
            if snapshot_vm_running:
                vm.start()
                vm.wait_for_login().close()
            ret = virsh.snapshot_create_as(vm_name, snapshot_name)
            libvirt.check_exit_status(ret)

        # Destroy VM first
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Build vm xml.
        dparams = {}
        if device_num > 1:
            for i in xrange(device_num):
                dparams[i] = {"rng_model": params.get(
                    "rng_model_%s" % i, "virtio")}
                dparams[i].update({"backend_model": params.get(
                    "backend_model_%s" % i, "random")})
                bk_type = params.get("backend_type_%s" % i)
                if bk_type:
                    dparams[i].update({"backend_type": bk_type})
                bk_dev = params.get("backend_dev_%s" % i)
                if bk_dev:
                    dparams[i].update({"backend_dev": bk_dev})
                bk_src = params.get("backend_source_%s" % i)
                if bk_src:
                    dparams[i].update({"backend_source": bk_src})
                bk_pro = params.get("backend_protocol_%s" % i)
                if bk_pro:
                    dparams[i].update({"backend_protocol": bk_pro})
                modify_rng_xml(dparams[i], False)
        else:
            modify_rng_xml(params, not test_snapshot)

        try:
            # Add random server
            if params.get("backend_type") == "tcp":
                cmd = "cat /dev/random | nc -4 -l localhost 1024"
                bgjob = utils.AsyncJob(cmd)

            # Start the VM.
            vm.start()
            if start_error:
                test.fail("VM started unexpectedly")

            if test_qemu_cmd:
                if device_num > 1:
                    for i in xrange(device_num):
                        check_qemu_cmd(dparams[i])
                else:
                    check_qemu_cmd(params)
            if test_host:
                check_host()
            session = vm.wait_for_login()
            if test_guest:
                check_guest(session)
            session.close()

            if test_snapshot:
                check_snapshot(bgjob)
        except virt_vm.VMStartError as details:
            logging.info(str(details))
            if not start_error:
                test.fail('VM failed to start, '
                          'please refer to https://bugzilla.'
                          'redhat.com/show_bug.cgi?id=1220252:'
                          '\n%s' % details)

    finally:
        # Delete snapshots.
        snapshot_lists = virsh.snapshot_list(vm_name)
        if len(snapshot_lists) > 0:
            libvirt.clean_up_snapshots(vm_name, snapshot_lists)
            for snapshot in snapshot_lists:
                virsh.snapshot_delete(vm_name, snapshot, "--metadata")

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        if bgjob:
            bgjob.kill_func()
