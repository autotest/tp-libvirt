import os
import re
import logging
from tempfile import mktemp
from avocado.core import exceptions
from virttest import virsh
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest import utils_misc

_FC_HOST_PATH = "/sys/class/fc_host"
_DELAY_TIME = 5


def check_nodedev(dev_name, dev_parent=None):
    """
    Check node device relevant values

    :params dev_name: name of the device
    :params dev_parent: parent name of the device, None is default
    :return: True if nodedev is normal.
    """
    host = dev_name.split("_")[1]
    fc_host_path = os.path.join(_FC_HOST_PATH, host)

    # Check if the /sys/class/fc_host/host$NUM exists
    if not os.access(fc_host_path, os.R_OK):
        logging.error("Can't access %s", fc_host_path)
        return False

    dev_xml = NodedevXML.new_from_dumpxml(dev_name)
    if not dev_xml:
        logging.error("Can't dumpxml %s XML", dev_name)
        return False

    # Check device parent name
    if dev_parent != dev_xml.parent:
        logging.error("The parent name is different: %s is not %s",
                      dev_parent, dev_xml.parent)
        return False

    wwnn_from_xml = dev_xml.wwnn
    wwpn_from_xml = dev_xml.wwpn
    fabric_wwn_from_xml = dev_xml.fabric_wwn

    fc_dict = {}
    name_list = ["node_name", "port_name", "fabric_name"]
    for name in name_list:
        fc_file = os.path.join(fc_host_path, name)
        with open(fc_file, "r") as fc_content:
            fc_dict[name] = fc_content.read().strip().split("0x")[1]

    # Check wwnn, wwpn and fabric_wwn
    if len(wwnn_from_xml) != 16:
        logging.error("The wwnn is not valid: %s", wwnn_from_xml)
        return False
    if len(wwpn_from_xml) != 16:
        logging.error("The wwpn is not valid: %s", wwpn_from_xml)
        return False
    if fc_dict["node_name"] != wwnn_from_xml:
        logging.error("The node name is differnet: %s is not %s",
                      fc_dict["node_name"], wwnn_from_xml)
        return False
    if fc_dict["port_name"] != wwpn_from_xml:
        logging.error("The port name is different: %s is not %s",
                      fc_dict["port_name"], wwpn_from_xml)
        return False
    if fc_dict["fabric_name"] != fabric_wwn_from_xml:
        logging.error("The fabric wwpn is differnt: %s is not %s",
                      fc_dict["fabric_name"], fabric_wwn_from_xml)
        return False

    fc_type_from_xml = dev_xml.fc_type
    cap_type_from_xml = dev_xml.cap_type

    # Check capability type
    if (cap_type_from_xml != "scsi_host") or (fc_type_from_xml != "fc_host"):
        logging.error("The capability type isn't 'scsi_host' or 'fc_host'")
        return False

    return True


def find_hbas(hba_type="hba", status="online"):
    """
    Find online hba/vhba cards.

    :params hba_type: "vhba" or "hba"
    :params status: "online" or "offline"
    :return: A list contains the online/offline vhba/hba list
    """
    # TODO: add offline/online judgement, fc storage not stable for now, so
    # leave this part after we buy npiv server
    result = virsh.nodedev_list(cap="scsi_host")
    if result.exit_status:
        raise exceptions.TestFail(result.stderr)

    scsi_hosts = result.stdout.strip().splitlines()
    online_hbas_list = []
    online_vhbas_list = []
    # go through all scsi hosts, and split hbas/vhbas into lists
    for scsi_host in scsi_hosts:
        result = virsh.nodedev_dumpxml(scsi_host)
        stdout = result.stdout.strip()
        if result.exit_status:
            raise exceptions.TestFail(result.stderr)
        if re.search('vport_ops', stdout) and not re.search('<fabric_wwn>'
                                                            'ffffffffffffffff</fabric_wwn>'
                                                            '', stdout):
            online_hbas_list.append(scsi_host)
        if re.search('fc_host', stdout) and not re.search('vport_ops', stdout):
            online_vhbas_list.append(scsi_host)
    if hba_type == "hba":
        return online_hbas_list
    if hba_type == "vhba":
        return online_vhbas_list


def is_vhbas_added(old_vhbas):
    """
    Check if a vhba is added

    :param old_vhbas: Pre-existing vhbas
    :return: True/False based on addition
    """
    new_vhbas = find_hbas("vhba")
    new_vhbas.sort()
    old_vhbas.sort()
    if len(new_vhbas) - len(old_vhbas) >= 1:
        return True
    else:
        return False


def is_vhbas_removed(old_vhbas):
    """
    Check if a vhba is removed

    :param old_vhbas: Pre-existing vhbas
    :return: True/False based on removal
    """
    new_vhbas = find_hbas("vhba")
    new_vhbas.sort()
    old_vhbas.sort()
    if len(new_vhbas) - len(old_vhbas) < 0:
        return True
    else:
        return False


def nodedev_create_from_xml(params):
    """
    Create a node device with a xml object.

    :param params: Including nodedev_parent, scsi_wwnn, scsi_wwpn set in xml
    :return: The scsi device name just created
    """
    nodedev_parent = params.get("nodedev_parent")
    scsi_wwnn = params.get("scsi_wwnn")
    scsi_wwpn = params.get("scsi_wwpn")
    status_error = params.get("status_error", "no")
    vhba_xml = NodedevXML()
    vhba_xml.cap_type = 'scsi_host'
    vhba_xml.fc_type = 'fc_host'
    vhba_xml.parent = nodedev_parent
    vhba_xml.wwnn = scsi_wwnn
    vhba_xml.wwpn = scsi_wwpn
    logging.debug("Prepare the nodedev XML: %s", vhba_xml)
    vhba_file = mktemp()
    with open(vhba_file, 'w') as xml_object:
        xml_object.write(str(vhba_xml))

    result = virsh.nodedev_create(vhba_file,
                                  debug=True,
                                  )
    status = result.exit_status

    # Remove temprorary file
    os.unlink(vhba_file)

    # Check status_error
    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            raise exceptions.TestFail("%d not a expected command "
                                      "return value", status)
    elif status_error == "no":
        if status:
            raise exceptions.TestFail(result.stderr)
        else:
            output = result.stdout.strip()
            logging.info(output)
            for scsi in output.split():
                if scsi.startswith('scsi_host'):
                    # Check node device
                    utils_misc.wait_for(
                        lambda: check_nodedev(scsi, nodedev_parent),
                        timeout=_DELAY_TIME)
                    if check_nodedev(scsi, nodedev_parent):
                        return scsi
                    else:
                        raise exceptions.TestFail(
                            "XML of vHBA card '%s' is not correct,"
                            "Please refer to log errors for detailed info" % scsi)


def nodedev_destroy(scsi_host, params={}):
    """
    Destroy a nodedev of scsi_host#.
    :param scsi_host: The scsi to destroy
    :param params: Contain status_error
    """
    status_error = params.get("status_error", "no")
    result = virsh.nodedev_destroy(scsi_host)
    logging.info("destroying scsi:%s", scsi_host)
    status = result.exit_status
    # Check status_error
    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            raise exceptions.TestFail("%d not a expected command "
                                      "return value", status)
    elif status_error == "no":
        if status:
            raise exceptions.TestFail(result.stderr)
        else:
            # Check nodedev value
            if not check_nodedev(scsi_host):
                logging.info(result.stdout.strip())
            else:
                raise exceptions.TestFail("The relevant directory still exists"
                                          "or mismatch with result")


def check_nodedev_exist(scsi_host):
    """
    Check if scsi_host# exist.
    :param scsi_host: The scsi host to be checked
    :return: True if scsi_host exist, False if not
    """
    host = scsi_host.split("_")[1]
    fc_host_path = os.path.join(_FC_HOST_PATH, host)
    if os.path.exists(fc_host_path):
        return True
    else:
        return False


def vhbas_cleanup(new_vhbas):
    """
    Clean up all vhbas.
    """
    vhbas = []
    logging.info("cleanup all vhbas of the test")
    vhbas = find_hbas("vhba")
    logging.info("OH, the online vhbas are %s", vhbas)
    for scsi_host in new_vhbas:
        nodedev_destroy(scsi_host)
    left_vhbas = find_hbas("vhba")
    if left_vhbas:
        logging.error("old vhbas are: %s", left_vhbas)
    logging.debug("all scsi_hosts destroyed: %s", new_vhbas)


def run(test, params, env):
    """
    NPIV test for vhba create/destroy, contains following sceraions:
    1. create a vhba then destroy it
    2. create and destroy vhba frequently
    3. create a lot of vhbas
    4. create some vhbas, then destroy part of them
    """
    destroy_number = int(params.get("destroy_number", 1))
    create_number = int(params.get("create_number", 1))
    left_number = create_number - destroy_number
    create_then_destroy = "yes" == params.get("create_then_destroy", "no")
    set_valid_wwn_to_first_vhba = "yes" == params.get(
        "set_valid_wwn_to_first_vhba", "no")
    scsi_wwnn = params.get("scsi_wwnn", "")
    scsi_wwpn = params.get("scsi_wwpn", "")
    online_hbas = []
    online_hbas = find_hbas("hba")
    random_wwnn = ""
    random_wwpn = ""
    new_vhbas = []
    if not online_hbas:
        raise exceptions.TestSkipError("Host doesn't have online hba cards")
    # use the first online hba as parent hba to all vhbas
    first_online_hba = online_hbas[0]
    try:
        for i in range(create_number):
            # steps in case 4: create some vhbas, and first vhba is valid,
            # rest vhbas with random wwns
            # set valid wwn to first vhba
            if set_valid_wwn_to_first_vhba and i == 0:
                logging.info("create %s round with valid wwn", i)
                old_vhbas = find_hbas("vhba")
                new_vhba = nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": scsi_wwnn,
                     "scsi_wwpn": scsi_wwpn})
                utils_misc.wait_for(
                    lambda: is_vhbas_added(old_vhbas), timeout=5)
                new_vhbas.append(new_vhba)
            # set random wwn to rest vhbas
            elif set_valid_wwn_to_first_vhba and i != 0:
                logging.info("create %s round with random wwn", i)
                old_vhbas = find_hbas("vhba")
                new_vhba = nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": random_wwnn,
                     "scsi_wwpn": random_wwpn})
                if not utils_misc.wait_for(lambda: is_vhbas_added(old_vhbas), timeout=5):
                    logging.error("vhba not successfully created")
                new_vhbas.append(new_vhba)
            # steps in cases 1,2,3: create vhbas
            # set wwn (valid or random depending on test case variables) to
            # vhbas
            else:
                old_vhbas = find_hbas("vhba")
                new_vhba = nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": scsi_wwnn,
                     "scsi_wwpn": scsi_wwpn})
                if not utils_misc.wait_for(lambda: is_vhbas_added(old_vhbas), timeout=5):
                    logging.error("vhba not successfully created")
                new_vhbas.append(new_vhba)
                logging.info("create %s round with valid/random wwn", i)

            # 1,2,3 destroy vhbas just after creation
            if create_then_destroy and destroy_number > 0:
                logging.info("destroy %s round", i)
                old_vhbas = find_hbas("vhba")
                nodedev_destroy(new_vhbas[-1])
                if not utils_misc.wait_for(lambda: is_vhbas_removed(old_vhbas), timeout=5):
                    logging.error("vhba not successfully destroyed")
                del new_vhbas[-1]
                destroy_number = destroy_number - 1
        # steps in case 4: destroy vhbas in a row
        if not create_then_destroy and destroy_number > 0:
            for i in range(destroy_number):
                logging.info("destroy %s round", i)
                old_vhbas = find_hbas("vhba")
                nodedev_destroy(new_vhbas[-1])
                if not utils_misc.wait_for(lambda: is_vhbas_removed(old_vhbas), timeout=5):
                    logging.error("vhba not successfully destroyed")
                del new_vhbas[-1]
                logging.info("list left: %s", new_vhbas)

        logging.info("left number is: %s", left_number)
        logging.info("length of new_vhbas: %d", len(new_vhbas))
    finally:
        # clean up all vhbas
        vhbas_cleanup(new_vhbas)
