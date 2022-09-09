import logging as log
from avocado.core import exceptions
from virttest import utils_misc
from virttest import utils_npiv

_FC_HOST_PATH = "/sys/class/fc_host"
_DELAY_TIME = 5


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    NPIV test for vhba create/destroy, contains following scenarios:
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
    online_hbas = utils_npiv.find_hbas("hba")
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
                old_vhbas = utils_npiv.find_hbas("vhba")
                new_vhba = utils_npiv.nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": scsi_wwnn,
                     "scsi_wwpn": scsi_wwpn})
                utils_misc.wait_for(
                    lambda: utils_npiv.is_vhbas_added(old_vhbas), timeout=5)
                new_vhbas.append(new_vhba)
            # set random wwn to rest vhbas
            elif set_valid_wwn_to_first_vhba and i != 0:
                logging.info("create %s round with random wwn", i)
                old_vhbas = utils_npiv.find_hbas("vhba")
                new_vhba = utils_npiv.nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": random_wwnn,
                     "scsi_wwpn": random_wwpn})
                if not utils_misc.wait_for(lambda: utils_npiv.is_vhbas_added(old_vhbas), timeout=5):
                    logging.error("vhba not successfully created")
                new_vhbas.append(new_vhba)
            # steps in cases 1,2,3: create vhbas
            # set wwn (valid or random depending on test case variables) to
            # vhbas
            else:
                old_vhbas = utils_npiv.find_hbas("vhba")
                new_vhba = utils_npiv.nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": scsi_wwnn,
                     "scsi_wwpn": scsi_wwpn})
                if not utils_misc.wait_for(lambda: utils_npiv.is_vhbas_added(old_vhbas), timeout=5):
                    logging.error("vhba not successfully created")
                new_vhbas.append(new_vhba)
                logging.info("create %s round with valid/random wwn", i)

            # 1,2,3 destroy vhbas just after creation
            if create_then_destroy and destroy_number > 0:
                logging.info("destroy %s round", i)
                old_vhbas = utils_npiv.find_hbas("vhba")
                utils_npiv.nodedev_destroy(new_vhbas[-1])
                if not utils_misc.wait_for(lambda: utils_npiv.is_vhbas_removed(old_vhbas), timeout=5):
                    logging.error("vhba not successfully destroyed")
                del new_vhbas[-1]
                destroy_number = destroy_number - 1
        # steps in case 4: destroy vhbas in a row
        if not create_then_destroy and destroy_number > 0:
            for i in range(destroy_number):
                logging.info("destroy %s round", i)
                old_vhbas = utils_npiv.find_hbas("vhba")
                utils_npiv.nodedev_destroy(new_vhbas[-1])
                if not utils_misc.wait_for(lambda: utils_npiv.is_vhbas_removed(old_vhbas), timeout=5):
                    logging.error("vhba not successfully destroyed")
                del new_vhbas[-1]
                logging.info("list left: %s", new_vhbas)

        logging.info("left number is: %s", left_number)
        logging.info("length of new_vhbas: %d", len(new_vhbas))
    finally:
        # clean up all vhbas
        utils_npiv.vhbas_cleanup(new_vhbas)
