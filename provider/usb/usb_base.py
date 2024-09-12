from avocado.utils import distro
from avocado.utils import process


def get_host_pkg_and_cmd():
    """
    Get package and related command on the host.

    :return: pkg_and_cmd, extra usb package name and command.
    """
    if int(distro.detect().version) <= 9:
        pkg_and_cmd = ("usbredir-server", "usbredirserver")
    else:
        pkg_and_cmd = ("usbredir-tools", "usbredirect")
    return pkg_and_cmd


def start_redirect_server(params, usb_cmd, vendor_id, product_id):
    """
    Start redirect server

    :param params: Dict with test params.
    :param usb_cmd, the usb command to confirm how to start server.
    :param vendor_id, vendor id.
    :param product_id, produce id.
    :return server_id, redirect server id
    """
    port_num = params.get("port_num")
    if usb_cmd == "usbredirserver":
        ps = process.SubProcess("usbredirserver -p {} {}:{}".format
                                (port_num, vendor_id, product_id),
                                shell=True)
    elif usb_cmd == "usbredirect":
        ps = process.SubProcess(
            "usbredirect --device {}:{} --as {}:{}".format
            (vendor_id, product_id, "127.0.0.1", port_num),
            shell=True)
    server_id = ps.start()
    return server_id


def kill_redirect_server(usb_cmd):
    """
    Kill redirect server

    :param usb_cmd, the usb command to kill server.
    """
    if 'server_id' in globals():
        process.run("killall {}".format(usb_cmd), ignore_status=True)
