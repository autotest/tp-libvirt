# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   virsh_snapshot_stress.py
#
#   SPDX-License-Identifier: GPL-2.0
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import time
import logging
from pathlib import Path

from avocado.utils import process
from virttest import virsh, utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def _cmd_ok(test, res, what):
    if res.exit_status != 0:
        test.fail(f"{what} failed: rc={res.exit_status}, err={res.stderr_text}")


def _is_selinux_enforcing():
    res = process.run("getenforce", shell=True, ignore_status=True)
    return res.exit_status == 0 and res.stdout_text.strip().lower() == "enforcing"


def _selinux_relabel_path(p: Path):
    if _is_selinux_enforcing():
        process.run(f"chcon -R -t virt_image_t {p}", shell=True, ignore_status=True)


def _restore_from_images_dir(test, images_dir: Path, dest: Path) -> bool:
    candidates = [images_dir / dest.name, images_dir / f"{dest.name}.backup"]
    for src in candidates:
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _cmd_ok(test, process.run(f"cp -f {src} {dest}", shell=True), f"cp {src} -> {dest}")
            _selinux_relabel_path(dest)
            logging.info("Restored missing disk '%s' from '%s'", dest, src)
            return True
    return False


def _ensure_file_exists(test, images_dir: Path, path: Path):
    if path.exists():
        return
    if not _restore_from_images_dir(test, images_dir, path):
        test.fail(
            f"Source image for '{path}' not found. Tried: "
            f"'{images_dir / path.name}' and '{images_dir / (path.name + '.backup')}'"
        )


def _start_vm_and_login(env, vm_name: str):
    vm = env.get_vm(vm_name)
    if not vm.is_alive():
        vm.start()
    vm.wait_for_login().close()


def _qemu_img_info(qemu_img: str, path: Path):
    return process.run(f"{qemu_img} info -U {path}", shell=True, ignore_status=True)


def _qemu_img_format(qemu_img: str, path: Path) -> str:
    res = _qemu_img_info(qemu_img, path)
    if res.exit_status != 0:
        return ""
    for ln in res.stdout_text.splitlines():
        ls = ln.strip().lower()
        if ls.startswith("file format:"):
            return ln.split(":", 1)[1].strip().lower()
    return ""


def _path_via_domblklist_inactive(test, vm_name: str, target_dev: str) -> Path:
    res = virsh.domblklist(vm_name, options="--details --inactive", debug=False)
    _cmd_ok(test, res, "virsh domblklist --details --inactive")
    for ln in res.stdout_text.splitlines():
        ln = ln.strip()
        if not ln or ln.lower().startswith("type"):
            continue
        parts = ln.split(None, 3)  # Type Device Target Source
        if len(parts) < 4:
            continue
        _type, _device, _target, _source = parts
        if _target == target_dev:
            return Path(_source)
    test.fail(f"Failed to find source path for target '{target_dev}' via domblklist")


def _get_disk_path_and_fmt(test, vm_name: str, target_dev: str, qemu_img: str):
    vmx = vm_xml.VMXML.new_from_dumpxml(vm_name)
    for d in (vmx.get_devices("disk") or []):
        tgt = (d.target or {}).get("dev")
        if tgt != target_dev:
            continue
        src_obj = d.source
        src_attrs = getattr(src_obj, "attrs", {}) if src_obj else {}
        current = src_attrs.get("file") or src_attrs.get("dev")
        if current:
            p = Path(current)
            return p, _qemu_img_format(qemu_img, p)
        break
    p = _path_via_domblklist_inactive(test, vm_name, target_dev)
    return p, _qemu_img_format(qemu_img, p)


def _retarget_disk_with_vmxml(vm_name: str, target_dev: str, new_path: Path):
    disk_dict = {
        "target": {"dev": target_dev},
        "type_name": "file",
        "source": {"attrs": {"file": str(new_path)}},
    }
    libvirt_vmxml.modify_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), "disk", disk_dict)


def _check_virsh_commands(vm_name: str, target_dev: str):
    virsh.domstats(vm_name, ignore_status=False)
    virsh.domblkinfo(vm_name, target_dev, ignore_status=False)
    virsh.snapshot_list(vm_name, ignore_status=False)


def setup_test(test, env, vm_name, target_dev, work_dir: Path, images_dir: Path, qemu_img: str):
    original_path, disk_fmt = _get_disk_path_and_fmt(test, vm_name, target_dev, qemu_img)
    logging.info("Original disk %s:%s -> %s (fmt=%s)", vm_name, target_dev, original_path, disk_fmt or "?")

    dest = work_dir / original_path.name
    work_dir.mkdir(parents=True, exist_ok=True)

    if str(original_path) == str(dest):
        if not dest.exists():
            logging.warning("Disk already pointed to work_dir, but file is missing. Restoring...")
            _ensure_file_exists(test, images_dir, dest)
        moved_path = dest
    else:
        if original_path.exists():
            if dest.exists():
                process.run(f"rm -f {dest}", shell=True, ignore_status=True)
            _cmd_ok(test, process.run(f"cp {original_path} {dest}", shell=True), f"cp {original_path} -> {dest}")
        else:
            logging.warning("Original disk '%s' is missing. Restoring straight into work_dir...", original_path)
            _ensure_file_exists(test, images_dir, dest)
        moved_path = dest

    _selinux_relabel_path(work_dir)
    _selinux_relabel_path(moved_path)

    _retarget_disk_with_vmxml(vm_name, target_dev, moved_path)
    _ensure_file_exists(test, images_dir, moved_path)
    _start_vm_and_login(env, vm_name)

    return original_path, moved_path, disk_fmt


def run_test(vm_name, snap_type, count, interval, target_dev):
    snapshot_opt = "--disk-only --atomic" if snap_type == "external" else ""
    for _ in range(1, count + 1):
        virsh.snapshot_create(vm_name, options=snapshot_opt, debug=True, ignore_status=False)
        if interval > 0:
            time.sleep(interval)

    utils_libvirtd.Libvirtd().restart()
    time.sleep(2)
    _check_virsh_commands(vm_name, target_dev)


def _delete_all_snapshots(vm_name: str, external: bool):
    while True:
        cur = process.run(f"virsh snapshot-current {vm_name} --name", shell=True, ignore_status=True)
        if cur.exit_status != 0 or not cur.stdout_text.strip():
            break
        opts = "--current --metadata --children" if external else "--current"
        process.run(f"virsh snapshot-delete {vm_name} {opts}", shell=True, ignore_status=True)


def teardown_test(test, env, vm_name, external, moved_path: Path, original_path: Path, backup_xml):
    try:
        _delete_all_snapshots(vm_name, external)
    finally:
        try:
            vm = env.get_vm(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)

            if moved_path and original_path and str(moved_path) != str(original_path):
                if original_path.exists():
                    process.run(f"rm -f {original_path}", shell=True, ignore_status=True)
                if moved_path.exists():
                    _cmd_ok(
                        test,
                        process.run(f"cp {moved_path} {original_path}", shell=True),
                        f"cp {moved_path} -> {original_path}",
                    )

            if backup_xml is not None:
                backup_xml.sync()

        except Exception as e:
            logging.warning("Teardown restore failed: %s", e)


def run(test, params, env):
    """
    SC1: 250 internal snapshots, restart libvirt, checks.
    SC2: 250 external snapshots (--disk-only), restart libvirt, checks.
    """
    vm_name = params.get("main_vm")
    snap_type = params.get("snapshot_type", "internal").strip().lower()
    count = int(params.get("snapshot_count", 250))
    interval = int(params.get("snapshot_interval", 1))
    target_dev = params.get("target_dev", "vda")
    work_dir = Path(params.get("work_dir", "/home/libvirt-work"))
    images_dir = Path(params.get("images_dir", "/var/lib/avocado/data/avocado-vt/images"))
    qemu_img = params.get("qemu_img_binary", "/usr/bin/qemu-img")

    _cmd_ok(test, process.run(f"mkdir -p {work_dir}", shell=True), f"mkdir -p {work_dir}")

    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    original_path = None
    moved_path = None
    try:
        original_path, moved_path, _disk_fmt = setup_test(
            test, env, vm_name, target_dev, work_dir, images_dir, qemu_img
        )
        run_test(vm_name, snap_type, count, interval, target_dev)
    finally:
        teardown_test(test, env, vm_name, snap_type == "external", moved_path, original_path, backup_xml)
