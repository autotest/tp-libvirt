from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import utils_package
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test the command virsh hostname

    (1) Call virsh hostname
    (2) Call virsh hostname with an unexpected option
    (3) Call virsh hostname with libvirtd service stop
    """

    hostname_result = process.run("hostname", shell=True, ignore_status=True)
    hostname = hostname_result.stdout_text.strip()

    remote_uri = params.get("remote_uri", None)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    session = None
    if remote_uri:
        session = vm.wait_for_login()
        hostname = session.cmd_output("hostname").strip()

    # Prepare libvirtd service on local
    check_libvirtd = "libvirtd" in params
    if check_libvirtd:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Start libvirtd on remote server
    if remote_uri:
        if not utils_package.package_install("libvirt", session):
            test.cancel("Failed to install libvirt on remote server")
        libvirtd = utils_libvirtd.Libvirtd(session=session)
        libvirtd.restart()

    # Run test case
    option = params.get("virsh_hostname_options")
    remote_ip = params.get("remote_ip")
    if remote_uri and remote_ip.count("EXAMPLE"):
        test.cancel("Pls configure rempte_ip first")
    remote_pwd = params.get("remote_pwd", None)
    remote_user = params.get("remote_user", "root")
    if remote_uri:
        ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

    hostname_test = virsh.hostname(option, uri=remote_uri,
                                   ignore_status=True,
                                   debug=True)
    status = 0
    if hostname_test == '':
        status = 1
        hostname_test = None

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Close session
    if session:
        session.close()

    # Check status_error
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            test.fail("Command 'virsh hostname %s' succeeded "
                      "(incorrect command)" % option)
    elif status_error == "no":
        if hostname != hostname_test:
            test.fail(
                "Virsh cmd gives hostname %s != %s." % (hostname_test, hostname))
        if status != 0:
            test.fail("Command 'virsh hostname %s' failed "
                      "(correct command)" % option)
