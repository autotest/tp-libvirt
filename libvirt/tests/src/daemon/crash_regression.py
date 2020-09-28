import os
import logging

from six.moves import xrange

from avocado.utils import path
from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest.utils_libvirtd import LibvirtdSession
from virttest.utils_libvirtd import Libvirtd
from virttest.libvirt_xml.xcepts import LibvirtXMLError
from virttest.libvirt_xml.vm_xml import VMXML, VMCPUXML, VMPMXML
from virttest.libvirt_xml.network_xml import NetworkXML, IPXML
from virttest.libvirt_xml.devices import interface


def run_destroy_console(params, libvirtd, vm):
    """
    Start a vm with console connected and then destroy it.
    """
    vm.start(autoconsole=True)
    virsh.destroy(vm.name)


def run_sig_segv(params, libvirtd, vm):
    """
    Kill libvirtd with signal SEGV.
    """
    process.run('pkill %s --signal 11' % libvirtd.service_exec)


def run_shutdown_console(params, libvirtd, vm):
    """
    Start a vm with console connected and then shut it down.
    """
    vm.start(autoconsole=True)
    vm.shutdown()


def run_restart_console(params, libvirtd, vm):
    """
    Start a vm with console connected and then restart daemon
    and send some keys using the console.
    """
    vm.start(autoconsole=True)
    libvirtd.restart()
    vm.session.sendline('hello')


def run_restart_save_restore(params, libvirtd, vm):
    """
    Save and restore a domain after restart daemon.
    """
    libvirtd.restart()
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    virsh.save(vm.name, save_path)
    virsh.restore(save_path)


def post_restart_save_restore(params, libvirtd, vm):
    """
    Cleanup for test restart_save_restore
    """
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    if os.path.exists(save_path):
        os.remove(save_path)


def run_mix_boot_order_os_boot(params, libvirtd, vm):
    """
    Define a domain mixing boot device and disk boot order.
    """
    vm_name = vm.name
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        if not vm_xml.os.boots:
            os_xml = vm_xml.os
            os_xml.boots = {'dev': 'hd'}
            vm_xml.os = os_xml
        else:
            logging.debug(vm_xml.os.boots)

        order = 0
        devices = vm_xml.devices
        for device in devices:
            if device.device_tag == 'disk':
                device.boot = order
                order += 1
        vm_xml.devices = devices

        try:
            vm_xml.sync()
        except LibvirtXMLError:
            pass
    finally:
        vm_xml_backup.sync()


def run_kill_virsh_while_managedsave(params, libvirtd, vm):
    """
    Kill virsh process when doing managedsave.
    """
    if not vm.is_alive():
        vm.start()
    process.run("virsh managedsave %s &" % (vm.name), shell=True)
    process.run("pkill -9 virsh", ignore_status=True)


def post_kill_virsh_while_managedsave(params, libvirtd, vm):
    """
    Cleanup for test kill_virsh_while_managedsave
    """
    virsh.managedsave_remove(vm.name)


def run_job_acquire(params, libvirtd, vm):
    """
    Save domain after queried block info
    """
    vm.start()
    res = virsh.qemu_monitor_command(vm.name, 'info block', '--hmp')
    logging.debug(res)
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    virsh.save(vm.name, save_path)
    vm.wait_for_shutdown()


def post_job_acquire(params, libvirtd, vm):
    """
    Cleanup for test job_acquire
    """
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    if os.path.exists(save_path):
        os.remove(save_path)


def run_invalid_interface(params, libvirtd, vm):
    """
    Define a domain with an invalid interface device.
    """
    vm_name = vm.name
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        iface_xml = interface.Interface('bridge')
        iface_xml.set_target({'dev': 'vnet'})
        devices = vm_xml.devices
        devices.append(iface_xml)
        vm_xml.devices = devices

        try:
            vm_xml.sync()
        except LibvirtXMLError:
            pass
    finally:
        vm_xml_backup.sync()


def run_invalid_mac_net(params, libvirtd, vm):
    """
    Start a network with all zero MAC.
    """
    net_xml = NetworkXML()
    net_xml.name = 'invalid_mac'
    net_xml.forward = {'mode': 'nat'}
    net_xml.mac = "00:00:00:00:00:00"
    ip_xml = IPXML(address='192.168.123.1')
    net_xml.ip = ip_xml
    virsh.create(net_xml.xml)
    virsh.net_destroy(net_xml.name)


def run_cpu_compare(params, libvirtd, vm):
    """
    Comprare a cpu without model property.
    """
    cpu_xml = VMCPUXML()
    cpu_xml.topology = {"sockets": 1, "cores": 1, "threads": 1}
    res = virsh.cpu_compare(cpu_xml.xml)
    logging.debug(res)


def run_pm_test(params, libvirtd, vm):
    """
    Destroy VM after executed a series of operations about S3 and save restore
    """

    vm_name = vm.name
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    try:
        pm_xml = VMPMXML()
        pm_xml.mem_enabled = 'yes'
        vm_xml.pm = pm_xml
        vm_xml.sync()
        vm.prepare_guest_agent()
        virsh.dompmsuspend(vm.name, 'mem')
        virsh.dompmwakeup(vm.name)
        virsh.save(vm.name, save_path)
        virsh.restore(save_path)
        virsh.dompmsuspend(vm.name, 'mem')
        virsh.save(vm.name, save_path)
        virsh.destroy(vm.name)
    finally:
        vm_xml_backup.sync()


def post_pm_test(params, libvirtd, vm):
    """
    Cleanup for pm_test.
    """
    save_path = os.path.join(data_dir.get_tmp_dir(), 'tmp.save')
    if os.path.exists(save_path):
        os.remove(save_path)


def run_restart_firewalld(params, libvirtd, vm):
    """
    Restart firewalld when starting libvirtd.
    """
    libvirtd.insert_break('qemuStateInitialize')
    libvirtd.restart(wait_for_working=False)
    libvirtd.wait_for_stop()
    logging.debug("Stopped at qemuStatInitialize. Back trace:")
    for line in libvirtd.back_trace():
        logging.debug(line)
    process.run('service firewalld restart')
    libvirtd.cont()


def run(test, params, env):
    """
    Run various regression tests and check whether libvirt daemon crashes.
    """
    func_name = 'run_' + params.get("func_name", "default")
    post_func_name = 'post_' + params.get("func_name", "default")
    repeat = int(params.get("repeat", "1"))
    vm_name = params.get("main_vm", "virt-tests-vm1")
    bug_url = params.get("bug_url", None)
    vm = env.get_vm(vm_name)
    # Run virtlogd foreground
    try:
        path.find_command('virtlogd')
        process.run("systemctl stop virtlogd", ignore_status=True)
        process.run("virtlogd -d")
    except path.CmdNotFoundError:
        pass
    libvirtd = LibvirtdSession(gdb=True)
    serv_tmp = "libvirt" if libvirtd.service_exec == "libvirtd" else libvirtd.service_exec
    process.run("rm -rf /var/run/libvirt/%s-*" % serv_tmp,
                shell=True, ignore_status=True)
    try:
        libvirtd.start()

        run_func = globals()[func_name]
        for i in xrange(repeat):
            run_func(params, libvirtd, vm)

        stopped = libvirtd.wait_for_stop(timeout=5)
        if stopped:
            logging.debug('Backtrace:')
            for line in libvirtd.back_trace():
                logging.debug(line)

            if bug_url:
                logging.error("You might met a regression bug. Please reference %s" % bug_url)

            test.fail("Libvirtd stops with %s" % libvirtd.bundle['stop-info'])

        if post_func_name in globals():
            post_func = globals()[post_func_name]
            post_func(params, libvirtd, vm)
    finally:
        try:
            path.find_command('virtlogd')
            process.run('pkill virtlogd', ignore_status=True)
            process.run('systemctl restart virtlogd.socket', ignore_status=True)
            Libvirtd("libvirtd.socket").restart()
        except path.CmdNotFoundError:
            pass
        libvirtd.exit()
