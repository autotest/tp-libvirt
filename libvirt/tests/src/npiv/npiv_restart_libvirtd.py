import logging
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_libvirtd
from virttest import utils_npiv as npiv


_TIMEOUT = 5


def restart_libvirtd_and_check_vhbaxml(scsi_host, test):
    """
    Check a vhba's xml before and after restart libvirtd. Return false
    if vhba's xml chnaged.
    """
    libvirtd = utils_libvirtd.Libvirtd()
    cmd_result = virsh.nodedev_dumpxml(scsi_host)
    scsi_host_xml = cmd_result.stdout.strip()
    if "<device>" not in scsi_host_xml:
        test.fail("node device %s has invalid xml: %s" %
                  (scsi_host, scsi_host_xml))
    libvirtd.restart()
    cmd_result = virsh.nodedev_dumpxml(scsi_host)
    scsi_host_xml_new = cmd_result.stdout.strip()
    if (scsi_host_xml == scsi_host_xml_new):
        logging.debug("vhba's xml is same before&after libvirtd restarted:\n%s"
                      % scsi_host_xml_new)
        return True
    logging.debug("vhba's xml is not same before&after libvirtd restarted "
                  "before: %s\nafter: %s" % (scsi_host_xml, scsi_host_xml_new))
    return False


def create_vhba(test, wwnn, wwpn, parent_scsi_host, method="virsh",
                fc_host_dir="/sys/class/fc_host"):
    """
    Create vhba, can use 'virsh nodedev-create' or echo wwpn:wwnn to
    a hba's vport_create.
    """
    if method == "virsh":
        npiv.nodedev_create_from_xml(
            {"nodedev_parent": parent_scsi_host,
             "scsi_wwnn": wwnn,
             "scsi_wwpn": wwpn})
    elif method == "echo":
        parent_host = parent_scsi_host.split("_")[1]
        cmd = "echo '%s:%s' > %s/%s/vport_create"\
              % (wwpn, wwnn, fc_host_dir, parent_host)
        cmd_result = process.run(cmd, shell=True)
        if cmd_result.exit_status:
            test.fail("Failed to use echo to create vhba.")
    else:
        test.fail("method must be 'virsh' or 'echo'")


def delete_vhba(test, scsi_host="", wwnn="", wwpn="", parent_scsi_host="",
                method="virsh", fc_host_dir="/sys/class/fc_host"):
    """
    Delete a vhba, can use 'virsh nodedev-destroy' or echo wwpn:wwnn to
    a hba's vport_delete.
    """
    if method == "virsh":
        logging.debug("using *virsh nodedev-destroy*")
        if not scsi_host:
            test.fail("A scsi host must be provided for "
                      "'virsh' command to delete vhba.")
        npiv.nodedev_destroy(scsi_host)
    elif method == "echo":
        logging.debug("using *echo*")
        if (not wwnn) or (not wwpn) or (not parent_scsi_host):
            test.fail("wwnn, wwpn and parent scsi must be "
                      "provided for 'echo' command to delete "
                      "vhba.")
        parent_host = parent_scsi_host.split("_")[1]
        cmd = "echo '%s:%s' > %s/%s/vport_delete"\
              % (wwpn, wwnn, fc_host_dir, parent_host)
        cmd_result = process.run(cmd, shell=True)
        if cmd_result.exit_status:
            test.fail("Failed to use echo to delete vhba.")
    else:
        test.fail("method must be 'virsh' or 'echo'")


def run(test, params, env):
    """
    Test steps:
    1. create a vhba, by 'echo' or 'virsh nodedev-create'
    2. restart libvirtd
    3. check the vhba xml info which created in step 1
    4. destroy the vhba, by 'echo' or 'virsh nodedev-destroy'
    """
    wwnn = params.get("wwnn", "ENTER.YOUR.WWNN")
    wwpn = params.get("wwpn", "ENTER.YOUR.WWPN")
    create_vhba_method = params.get("create_vhba_method", "vish")
    destroy_vhba_method = params.get("destroy_vhba_method", "virsh")
    fc_host_dir = params.get("fc_host_dir", "/sys/class/fc_host")

    online_hbas = []
    first_online_hba = ""
    old_vhbas = []
    new_vhba = ""
    new_vhbas = []
    cur_vhbas = []

    try:
        online_hbas = npiv.find_hbas("hba")
        if not online_hbas:
            test.cancel("NO ONLINE VHBAs!")
        first_online_hba = online_hbas[0]
        old_vhbas = npiv.find_hbas("vhba")

        # Create vhba
        create_vhba(test, wwnn, wwpn, first_online_hba, create_vhba_method,
                    fc_host_dir)
        if not utils_misc.wait_for(lambda: npiv.is_vhbas_added(old_vhbas),
                                   timeout=_TIMEOUT):
            test.fail("vhba not successfully created")
        time.sleep(2)
        tmp_list = list(set(npiv.find_hbas("vhba")).difference(set(old_vhbas)))
        if len(tmp_list) != 1:
            test.fail("Not 1 vhba created, something wrong.")
        new_vhba = tmp_list[0]
        logging.debug("Newly added vhba is: %s" % new_vhba)
        new_vhbas.append(new_vhba)

        # Restart libvirtd, and check vhba's xml after it.
        check_result = restart_libvirtd_and_check_vhbaxml(new_vhba, test)
        if not check_result:
            test.fail("vhba %s's xml changed after libvirtd "
                      "restarted." % new_vhba)

        # Destroy vhba
        cur_vhbas = npiv.find_hbas("vhba")
        delete_vhba(test, new_vhba, wwnn, wwpn, first_online_hba,
                    destroy_vhba_method, fc_host_dir)
        if not utils_misc.wait_for(lambda: npiv.is_vhbas_removed(cur_vhbas),
                                   timeout=_TIMEOUT):
            test.fail("Failed to destroy vhba.")
        if npiv.check_nodedev_exist(new_vhba):
            test.fail("Failed to destroy vhba %s" % new_vhba)
        else:
            new_vhbas.remove(new_vhba)

    finally:
        if new_vhbas:
            for vhba in new_vhbas:
                npiv.nodedev_destroy(vhba)
        cmd_status = process.system('service multipathd restart', verbose=True)
        if cmd_status:
            logging.error("Something wrong when restart multipathd.")
