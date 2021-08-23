from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_sriov


def setup_vf(pf_pci, params):
    """
    Enable vf setting

    :param pf_pci: The pci of PF
    :return: The original vf value
    """
    default_vf = 0
    try:
        vf_no = int(params.get("vf_no", "4"))
    except ValueError as e:
        raise exceptions.TestError(e)
    pf_pci_path = utils_misc.get_pci_path(pf_pci)
    cmd = "cat %s/sriov_numvfs" % (pf_pci_path)
    default_vf = process.run(cmd, shell=True, verbose=True).stdout_text
    if not utils_sriov.set_vf(pf_pci_path, vf_no):
        raise exceptions.TestError("Failed to set vf.")
    if not utils_misc.wait_for(lambda: process.run(
       cmd, shell=True, verbose=True).stdout_text.strip() == str(vf_no),
       30, 5):
        raise exceptions.TestError("VF's number should set to %d." % vf_no)
    return default_vf


def recover_vf(pf_pci, params, default_vf=0):
    """
    Recover vf setting

    :param pf_pci: The pci of PF
    :param params: the parameters dict
    :param default_vf: The value to be set
    """
    pf_pci_path = utils_misc.get_pci_path(pf_pci)
    vf_no = int(params.get("vf_no", "4"))
    if default_vf != vf_no:
        utils_sriov.set_vf(pf_pci_path, default_vf)
