import shutil
import time
import logging
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from avocado.utils import process
from virttest import virsh, utils_libvirtd, utils_selinux
from virttest.utils_libvirt import libvirt_disk
from virttest.libvirt_xml import vm_xml


def _cmd_ok(test, res, what):
    if res.exit_status != 0:
        test.fail(f"{what} failed: rc={res.exit_status}, err={res.stderr_text}")


def _is_selinux_enforcing():
    return str(utils_selinux.get_status()).strip().lower() == "enforcing"


def _selinux_relabel_path(p):
    if _is_selinux_enforcing():
        process.run(f"chcon -R -t virt_image_t {p}", shell=True, ignore_status=True)


def _start_vm_and_login(vm):
    if not vm.is_alive():
        vm.start()
    vm.wait_for_login().close()


def _get_disk_path(env, vm_name):
    vm = env.get_vm(vm_name)
    return Path(libvirt_disk.get_first_disk_source(vm))


def _define_from_xml_str(test, xml_str):
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(xml_str)
        tmp = f.name
    try:
        res = virsh.define(tmp, debug=True, ignore_status=True)
        _cmd_ok(test, res, f"virsh define {tmp}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _retarget_disk_persistent_xml(test, vm_name, target_dev, new_path):
    res = virsh.dumpxml(vm_name, options="--inactive", ignore_status=False, debug=True)
    xml_str = res.stdout_text
    root = ET.fromstring(xml_str)
    devices = root.find("devices")
    found = False
    if devices is not None:
        for disk in devices.findall("disk"):
            target = disk.find("target")
            if target is not None and target.get("dev") == target_dev:
                source = disk.find("source") or ET.SubElement(disk, "source")
                source.set("file", str(new_path))
                disk.set("type", "file")
                found = True
                break
    if not found:
        test.fail(f"Target '{target_dev}' not found in inactive XML")
    new_xml = ET.tostring(root, encoding="unicode")
    _define_from_xml_str(test, new_xml)


def _check_virsh_commands(vm_name, target_dev):
    virsh.domstats(vm_name, ignore_status=False, debug=True)
    virsh.domblkinfo(vm_name, target_dev, ignore_status=False, debug=True)
    virsh.snapshot_list(vm_name, ignore_status=False, debug=True)


def _try_restore_from_images_dir(src_name: str, images_dir: Path, dest: Path) -> bool:
    candidates = [
        images_dir / src_name,
        images_dir / "base_images" / src_name,
    ]
    for cand in candidates:
        if cand.exists():
            try:
                shutil.copyfile(cand, dest)
                logging.info("Restored image from '%s' -> '%s'", cand, dest)
                return True
            except Exception as e:
                logging.warning("Failed to restore from '%s' -> '%s': %s", cand, dest, e)
    return False


def setup_test(test, env, vm_name, target_dev, work_dir, images_dir):
    original_path = _get_disk_path(env, vm_name)
    logging.info("Original disk %s:%s -> %s", vm_name, target_dev, original_path)

    work_dir = Path(work_dir)
    images_dir = Path(images_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    dest = work_dir / original_path.name

    if original_path == dest:
        if not dest.exists():
            if not _try_restore_from_images_dir(dest.name, images_dir, dest):
                test.fail(f"Image '{dest}' is missing (already targeting work_dir) and restore failed.")
        copied_path = dest
    else:
        try:
            dest.unlink(missing_ok=True)
            shutil.copyfile(original_path, dest)
        except Exception as e:
            logging.warning("Copy '%s' -> '%s' failed (%s). Attempting restore from images_dir...", original_path, dest, e)
            if not _try_restore_from_images_dir(dest.name, images_dir, dest):
                test.fail(f"Failed to prepare working image '{dest}': {e}")
        copied_path = dest

    _selinux_relabel_path(work_dir)
    _selinux_relabel_path(copied_path)

    _retarget_disk_persistent_xml(test, vm_name, target_dev, copied_path)

    if not copied_path.exists():
        test.fail(f"Expected image at '{copied_path}' after copy/restore, but file is missing")

    vm = env.get_vm(vm_name)
    _start_vm_and_login(vm)

    return original_path, copied_path


def run_test(vm_name, snap_type, count, interval, target_dev):
    snapshot_opt = "--disk-only" if snap_type == "external" else ""
    for _ in range(count):
        virsh.snapshot_create(vm_name, options=snapshot_opt, debug=True, ignore_status=False)
        if interval > 0:
            time.sleep(interval)

    utils_libvirtd.Libvirtd().restart()
    time.sleep(2)
    _check_virsh_commands(vm_name, target_dev)


def _delete_all_snapshots(vm_name, external):
    while True:
        cur = virsh.snapshot_current(vm_name, options="--name", ignore_status=True, debug=True)
        snap = cur.stdout_text.strip() if cur.exit_status == 0 else ""
        if not snap:
            break
        if external:
            virsh.snapshot_delete(vm_name, snap, options="--metadata", ignore_status=False, debug=True)
        else:
            virsh.snapshot_delete(vm_name, snap, ignore_status=False, debug=True)


def teardown_test(test, vm, vm_name, external, copied_path, original_path, bkxml):
    try:
        _delete_all_snapshots(vm_name, external)
    finally:
        try:
            if vm is not None and vm.is_alive():
                vm.destroy(gracefully=False)
            if copied_path and original_path and copied_path != original_path:
                Path(copied_path).unlink(missing_ok=True)
            if bkxml is not None:
                try:
                    bkxml.sync()
                except Exception as e:
                    test.fail(f"Failed to restore VM XML via VMXML: {e}")
        except Exception:
            raise


def run(test, params, env):
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if vm is None:
        test.fail(f"VM '{vm_name}' not found in env")

    snap_type = params.get("snapshot_type", "internal").strip().lower()
    count = int(params.get("snapshot_count", 250))
    interval = int(params.get("snapshot_interval", 1))
    target_dev = params.get("target_dev", "vda")
    work_dir = Path(params.get("work_dir", "/home/libvirt-work"))
    images_dir = Path(params.get("images_dir", "/var/lib/avocado/data/avocado-vt/images"))

    work_dir.mkdir(parents=True, exist_ok=True)

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name).copy()

    original_path = None
    copied_path = None
    try:
        original_path, copied_path = setup_test(
            test, env, vm_name, target_dev, work_dir, images_dir
        )
        run_test(vm_name, snap_type, count, interval, target_dev)
    finally:
        teardown_test(
            test, vm, vm_name, snap_type == "external", copied_path, original_path, bkxml
        )
