from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
import logging
import shutil
import os
import re


def prepare_image(params):
    """
    (1) Create a image
    (2) Create file system on the image
    """
    params["image_path"] = utils_test.libguestfs.preprocess_image(params)

    if not params.get("image_path"):
        raise error.TestFail("Image could not be created for some reason.")

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        raise error.TestFail(output)
    gf.close_session()


def create_lvm(gf, mode, pv_name="/dev/sda", vg_name="VG", lv_name="LV", size=100):

    if mode == 'pvcreate':
        gf.part_init(pv_name, "msdos")
        gf.pvcreate(pv_name)
        ret = gf.pvs().stdout.strip()
        if not ret:
            gf.close_session()
            raise error.TestFail("create PV failed")
        return ret
    elif mode == 'vgcreate':
        gf.vgcreate(vg_name, pv_name)
        ret = gf.vgs().stdout.strip()
        if not ret:
            gf.close_session()
            raise error.TestFail("create VG failed")
        return ret
    elif mode == 'lvcreate':
        gf.lvcreate(lv_name, vg_name, size)
        ret = gf.lvs().stdout.strip()
        if not ret:
            gf.close_session()
            raise error.TestFail("create LV failed")
        return ret
    else:
        logging.info("mode should be 'pvcreate','vgcreate' or 'lvcreate'")


def test_is_lv(vm, params):
    """
    Test command is-lv
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    # check physical device
    name = gf.list_partitions().stdout.strip()
    ret = gf.is_lv(name).stdout.strip()
    if ret != "false":
        gf.close_session()
        raise error.TestFail("It should be a physical device")

    # check lvm device
    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    name = gf.lvs().stdout.strip()
    ret = gf.is_lv(name).stdout.strip()
    if ret != "true":
        gf.close_session()
        raise error.TestFail("It should be a lvm device")

    gf.close_session()


def test_lvcreate(vm, params):
    """
    Test command lvcreate
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")

    vg_name = "myvg"
    lv_name = "mylv"

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate', vg_name=vg_name)
    create_lvm(gf, 'lvcreate', vg_name=vg_name, lv_name=lv_name)

    part_name = "/dev/%s/%s" % (vg_name, lv_name)

    result = gf.lvs().stdout.strip()

    if result != part_name:
        gf.close_session()
        raise error.TestFail("lv name is not match")

    result = gf.lvs_full().stdout.strip()
    result = re.search("lv_name:\s+(\S+)", result).groups()[0]

    if result != lv_name:
        gf.close_session()
        raise error.TestFail("lv name is not match")

    gf.close_session()


def test_lvm_canonical_lv_name(vm, params):
    """
    Test command lvm-canonical-lv-name
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    real_name = gf.lvs().stdout.strip()
    vg_name, lv_name = real_name.split("/")[-2:]

    test_name = "/dev/mapper/%s-%s" % (vg_name, lv_name)
    result = gf.lvm_canonical_lv_name(test_name).stdout.strip()
    logging.debug(result)

    if result != real_name:
        gf.close_session()
        raise error.TestFail("Return name is uncorrect")

    gf.close_session()


def test_lvremove(vm, params):
    """
    Test command lvremove
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    ret = gf.lvs().stdout.strip()
    logging.debug(ret)
    if ret:
        gf.lvremove(ret)

    ret = gf.lvs().stdout.strip()
    if ret:
        gf.close_session()
        raise error.TestFail("LV can't be removed")

    gf.close_session()


def test_lvm_remove_all(vm, params):
    """
    Test command lvm-remove-all
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    pv = gf.pvs().stdout.strip()
    vg = gf.vgs().stdout.strip()
    lv = gf.lvs().stdout.strip()
    logging.debug("pv: %s\n vg:%s\n lv:%s\n" % (pv, vg, lv))

    if pv and vg and lv:
        gf.lvm_remove_all()

    pv = gf.pvs().stdout.strip()
    vg = gf.vgs().stdout.strip()
    lv = gf.lvs().stdout.strip()
    logging.debug("pv: %s\n vg:%s\n lv:%s\n" % (pv, vg, lv))

    if pv or vg or lv:
        gf.close_session()
        raise error.TestFail("lvm-remove-all failed")

    gf.close_session()


def test_lvrename(vm, params):
    """
    Test command lvrename
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    ret = gf.lvs().stdout.strip()
    logging.debug(ret)
    if ret:
        new_lv_name = "newlv"
        gf.lvrename(ret, new_lv_name)

    ret = gf.lvs().stdout.strip()
    if new_lv_name not in ret:
        gf.close_session()
        raise error.TestFail("LV can't be renamed")

    gf.close_session()


def test_lvresize(vm, params):
    """
    Test command lvresize
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    ret = gf.lvs_full().stdout.strip()
    lv = gf.lvs().stdout.strip()
    old_size = re.search("lv_size:\s+(\S+)", ret).groups()[0]

    ret = gf.lvresize(lv, 200)
    if ret.exit_status:
        gf.close_session()
        raise error.TestFail("lvresize execute failed")

    ret = gf.lvs_full().stdout.strip()
    new_size = re.search("lv_size:\s+(\S+)", ret).groups()[0]

    logging.debug("old_size is %s, new_size is %s" % (old_size, new_size))

    if new_size <= old_size:
        gf.close_session()
        raise error.TestFail("lvresize failed")

    gf.close_session()


def test_lvresize_free(vm, params):
    """
    Test command lvresize-free
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate', size=200)

    lv = gf.lvs().stdout.strip()

    ret = gf.lvs_full().stdout.strip()
    old_size = re.search("lv_size:\s+(\S+)", ret).groups()[0]
    ret = gf.vgs_full().stdout.strip()
    max_size = re.search("vg_size:\s+(\S+)", ret).groups()[0]

    ret = gf.lvresize_free(lv, 100)
    if ret.exit_status:
        gf.close_session()
        raise error.TestFail("lvresize-free execute failed")

    ret = gf.lvs_full().stdout.strip()
    new_size = re.search("lv_size:\s+(\S+)", ret).groups()[0]

    logging.debug("old_size is %s, new_size is %s" % (old_size, new_size))

    if new_size != max_size:
        gf.close_session()
        raise error.TestFail("lv_size should be %s" % max_size)

    gf.close_session()


def test_lvm_set_filter(vm, params):
    """
    Test command lvm-set-filter and lvm-clear-filter
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    lv_name = gf.lvs().stdout.strip()
    if not lv_name:
        gf.close_session()
        raise error.TestFail("LV should be listed")

    # set filter, lvm device should be hided
    gf.lvm_set_filter(lv_name)
    lv_name = gf.lvs().stdout.strip()
    if lv_name:
        gf.close_session()
        raise error.TestFail("LV should not be listed")

    # clear the filter, lvm device can be seen
    gf.lvm_clear_filter()
    lv_name = gf.lvs().stdout.strip()
    if not lv_name:
        gf.close_session()
        raise error.TestFail("LV should be listed")

    gf.close_session()


def test_lvuuid(vm, params):
    """
    Test command lvuuid
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    lv_name = gf.lvs().stdout.strip()
    uuid = gf.lvuuid(lv_name).stdout.strip()
    uuid = re.sub("-", "", uuid)
    logging.debug("uuid from lvuuid is %s" % uuid)

    ret = gf.lvs_full().stdout.strip()
    result = re.search("lv_uuid:\s+(\S+)", ret).groups()[0]
    logging.debug("uuid from lvs-full is %s" % result)

    if uuid != result:
        gf.close_session()
        raise error.TestFail("lv uuid is not match")

    gf.close_session()


def test_vgcreate(vm, params):
    """
    Test command vgcreate
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")

    vg_name = "myvg"
    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate', vg_name=vg_name)

    result = gf.vgs().stdout.strip()

    if result != vg_name:
        gf.close_session()
        raise error.TestFail("vg name is not match")

    ret = gf.vgs_full().stdout.strip()
    result = re.search("vg_name:\s+(\S+)", ret).groups()[0]

    if result != vg_name:
        gf.close_session()
        raise error.TestFail("vg name is not match")

    gf.close_session()


def test_vgremove(vm, params):
    """
    Test command vgremove
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    ret = gf.vgs().stdout.strip()
    logging.debug(ret)
    if ret:
        gf.vgremove(ret)

    ret = gf.vgs().stdout.strip()
    if ret:
        gf.close_session()
        raise error.TestFail("VG can't be removed")

    gf.close_session()


def test_vgrename(vm, params):
    """
    Test command vgrename
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    ret = gf.vgs().stdout.strip()
    logging.debug(ret)
    if ret:
        new_vg_name = "newvg"
        gf.vgrename(ret, new_vg_name)

    ret = gf.vgs().stdout.strip()
    if new_vg_name not in ret:
        gf.close_session()
        raise error.TestFail("VG can't be renamed")

    gf.close_session()


def test_vgscan(vm, params):
    """
    Test command vgscan
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    result = gf.vgscan()
    if result.exit_status:
        gf.close_session()
        raise error.TestFail("vgscan execute failed")

    gf.close_session()


def test_vguuid(vm, params):
    """
    Test command vguuid
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    vg_name = gf.vgs().stdout.strip()
    uuid = gf.vguuid(vg_name).stdout.strip()
    uuid = re.sub("-", "", uuid)
    logging.debug("uuid from vguuid is %s" % uuid)

    ret = gf.vgs_full().stdout.strip()
    result = re.search("vg_uuid:\s+(\S+)", ret).groups()[0]
    logging.debug("uuid from lvs-full is %s" % result)

    if uuid != result:
        gf.close_session()
        raise error.TestFail("vg uuid is not match")

    gf.close_session()


def test_vg_activate(vm, params):
    """
    Test command vg-activate
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    vg_name = gf.vgs().stdout.strip()
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name not in result:
        gf.close_session()
        raise error.TestFail("Can not find %s in /dev" % vg_name)

    gf.vg_activate(0, vg_name)
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name in result:
        gf.close_session()
        raise error.TestFail("Find %s in /dev, it shouldn't be" % vg_name)

    gf.vg_activate(1, vg_name)
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name not in result:
        gf.close_session()
        raise error.TestFail("Can not find %s in /dev" % vg_name)

    gf.close_session()


def test_vg_activate_all(vm, params):
    """
    Test command vg-activate-all
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    vg_name = gf.vgs().stdout.strip()
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name not in result:
        gf.close_session()
        raise error.TestFail("Can not find %s in /dev" % vg_name)

    gf.vg_activate_all(0)
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name in result:
        gf.close_session()
        raise error.TestFail("Find %s in /dev, it shouldn't be" % vg_name)

    gf.vg_activate_all(1)
    result = gf.debug("ls", "/dev").stdout.strip()
    if vg_name not in result:
        gf.close_session()
        raise error.TestFail("Can not find %s in /dev" % vg_name)

    gf.close_session()


def test_vglvuuids(vm, params):
    """
    Test command vglvuuids
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    lv_name = gf.lvs().stdout.strip()
    uuid = gf.lvuuid(lv_name).stdout.strip()

    result = gf.vglvuuids('VG').stdout.strip()

    if uuid != result:
        gf.close_session()
        raise error.TestFail("lv uuid is not match")

    gf.close_session()


def test_vgpvuuids(vm, params):
    """
    Test command vgpvuuids
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    create_lvm(gf, 'vgcreate')
    create_lvm(gf, 'lvcreate')

    pv_name = gf.pvs().stdout.strip()
    uuid = gf.pvuuid(pv_name).stdout.strip()

    result = gf.vgpvuuids('VG').stdout.strip()

    if uuid != result:
        gf.close_session()
        raise error.TestFail("pv uuid is not match")

    gf.close_session()


def test_pvcreate(vm, params):
    """
    Test command pvcreate
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    pv_name = gf.pvs().stdout.strip()

    result = gf.pvs_full().stdout.strip()
    result = re.search("pv_name:\s+(\S+)", result).groups()[0]

    if result != pv_name != "/dev/sda":
        gf.close_session()
        raise error.TestFail("pv name is not match")

    gf.close_session()


def test_pvremove(vm, params):
    """
    Test command pvremove
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    pv_name = gf.pvs().stdout.strip()

    if pv_name != "/dev/sda":
        gf.close_session()
        raise error.TestFail("pv name is not match")

    gf.pvremove('/dev/sda')
    pv_name = gf.pvs().stdout.strip()

    if pv_name:
        gf.close_session()
        raise error.TestFail("remove pv failed")

    gf.close_session()


def test_pvresize(vm, params):
    """
    Test command pvresize and pvresize-size
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')
    result = gf.pvs_full().stdout.strip()
    pv_size = re.search("pv_size:\s+(\S+)", result).groups()[0]

    new_size = pv_size[:-1]
    gf.pvresize_size("/dev/sda", new_size)

    result = gf.pvs_full().stdout.strip()
    get_size = re.search("pv_size:\s+(\S+)", result).groups()[0]

    if get_size != new_size:
        gf.close_session()
        raise error.TestFail("Can not get correct size via pvresize-size")

    gf.pvresize("/dev/sda")

    result = gf.pvs_full().stdout.strip()
    get_size = re.search("pv_size:\s+(\S+)", result).groups()[0]

    if get_size != pv_size:
        gf.close_session()
        raise error.TestFail("Can not get correct size via pvresize-size")

    gf.close_session()


def test_pvuuid(vm, params):
    """
    Test command pvuuid
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    create_lvm(gf, 'pvcreate')

    pv_name = gf.pvs().stdout.strip()
    uuid = gf.pvuuid(pv_name).stdout.strip()
    uuid = re.sub("-", "", uuid)
    logging.debug("uuid from pvuuid is %s" % uuid)

    ret = gf.pvs_full().stdout.strip()
    result = re.search("pv_uuid:\s+(\S+)", ret).groups()[0]
    logging.debug("uuid from pvs-full is %s" % result)

    if uuid != result:
        gf.close_session()
        raise error.TestFail("pv uuid is not match")

    gf.close_session()


def run(test, params, env):
    """
    Test of built-in lvm related commands in guestfish.

    1) Get parameters for test
    2) Set options for commands
    3) Run key commands:
       a.add disk or domain with readonly or not
       b.launch
       c.mount root device
    4) Write a file to help result checking
    5) Check result
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()

    operation = params.get("guestfish_function")
    testcase = globals()["test_%s" % operation]
    partition_types = params.get("partition_types")
    fs_types = params.get("fs_types")
    image_formats = params.get("image_formats")

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            prepare_image(params)
            testcase(vm, params)
