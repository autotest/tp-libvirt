# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen <chwen@redhat.com>
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import logging
import re
import os
import shutil

from virttest import virsh
from provider.bootc_image_builder import bootc_image_build_utils as bib_utils

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def update_bib_env_info(params, test):
    """
    Common method to update environment when image build output exists

    :param params: class params representing the test parameters
    :param test: test object
    """
    base_folder = params.get("output_base_folder")
    libvirt_base_folder = params.get("libvirt_base_folder")
    output_sub_folder = params.get("output_sub_folder")
    output_name = params.get("output_name")
    bib_ref = params.get("bib_ref")
    firmware = params.get("firmware")

    full_path = os.path.join(base_folder, output_sub_folder, output_name)
    if not os.path.exists(full_path):
        test.fail("bootc image build fail to generate outputs for image type: %s" % params.get("disk_image_type"))
    converted_disk_image = f"install_{bib_ref}_{firmware}_{bib_utils.convert_disk_image_name(params)}"
    disk_name, _ = os.path.splitext(converted_disk_image)
    full_path_dest = os.path.join(libvirt_base_folder, converted_disk_image)
    shutil.move(full_path, full_path_dest)
    LOG.debug("vm_disk_image_path: %s", full_path_dest)
    LOG.debug("vm_name_bootc: %s", disk_name)
    cleanup_files.append(full_path_dest)
    params.update({'vm_disk_image_path': full_path_dest})
    params.update({'vm_name_bootc': disk_name})

    iso_install_path = os.path.join(libvirt_base_folder, f"{disk_name}_{firmware}.qcow2")
    params.update({'iso_install_path': iso_install_path})
    cleanup_files.append(iso_install_path)


def prepare_env_and_execute_bib(params, test):
    """
    One method to prepare environment for image build

    :param params: class params representing the test parameters
    :param test: test object
    """
    disk_image_type = params.get("disk_image_type")
    bib_image_url = params.get("bib_image_url", "quay.io/centos-bootc/bootc-image-builder:latest")
    image_ref = params.get("image_ref")
    bib_ref = params.get("bib_ref")
    container_url = params.get("container_url")
    local_container = "yes" == params.get("local_container")
    build_container = params.get("build_container")

    enable_tls_verify = params.get("enable_tls_verify", "true")
    ownership = params.get("ownership")
    key_store_mounted = params.get("key_store_mounted")
    roofs = params.get("roofs")
    aws_config_dict = eval(params.get("aws_config_dict", '{}'))
    options = None

    bib_utils.install_bib_packages()
    config_json_file = bib_utils.create_config_json_file(params)
    if disk_image_type in ["ami"]:
        bib_utils.prepare_aws_env(params)

    if bib_ref in ["upstream_bib", "rhel_9.4_nightly_bib", "rhel_9.5_nightly_bib", "rhel_10.0_bib"]:
        auth_file = bib_utils.create_auth_json_file(params)
        bib_utils.podman_login_with_auth(auth_file, params.get("redhat_registry"))
        options = auth_file
        bib_utils.podman_login(params.get("podman_stage_username"), params.get("podman_stage_password"),
                               params.get("redhat_registry"))

    # pull base image and build local image after change
    if build_container:
        if bib_ref == "rhel_9.4_bib":
            bib_utils.podman_login(params.get("podman_redhat_username"), params.get("podman_redhat_password"),
                                   params.get("redhat_registry"))
        bib_utils.create_and_build_container_file(params)
    if bib_ref == "rhel_9.4_bib":
        ownership = None
        bib_utils.podman_push(params.get("podman_quay_username"), params.get("podman_quay_password"),
                              params.get("registry"), container_url)

    result = bib_utils.podman_command_build(bib_image_url, disk_image_type, container_url, config_json_file,
                                            local_container, enable_tls_verify, ownership,
                                            key_store_mounted, None, roofs, options, **aws_config_dict)
    if disk_image_type in ['ami'] and len(aws_config_dict) != 0:
        match_ami_id_obj = re.search(r'AMI registered:\s(.*)', result.stdout_text)
        if match_ami_id_obj is None:
            test.fail("Failed to get AWS AMI id")
        aws_ami_id = match_ami_id_obj.group(1).strip()
        LOG.debug(f"aws_ami_id is: {aws_ami_id}")
        params.update({"aws_ami_id": aws_ami_id})
        match_aws_ami_snapshot_id_obj = re.search(r'Snapshot ID:\s(.*)', result.stdout_text)
        if match_aws_ami_snapshot_id_obj is None:
            test.fail("Failed to get AWS AMI snapshot id")
        aws_ami_snapshot_id = match_aws_ami_snapshot_id_obj.group(1).strip()
        LOG.debug(f"aws_ami_snapshot_id is: {aws_ami_snapshot_id}")
        params.update({"aws_ami_snapshot_id": aws_ami_snapshot_id})


def run(test, params, env):
    """
    Test install disk image generated by boot container image builder.
    Add existing BOOTABLE image to PODMAN (cover RHEL, FEDORA)
    Build image from BOOTABLE image with PODMAAN (AWS, QCOW2,RAW, ISO).
    Validate install built out images
    """
    disk_image_type = params.get("disk_image_type")
    aws_config_dict = eval(params.get("aws_config_dict", '{}'))
    try:
        prepare_env_and_execute_bib(params, test)
        # validate build output
        update_bib_env_info(params, test)
        if disk_image_type in ["vmdk"]:
            bib_utils.create_and_start_vmware_vm(params)
        elif disk_image_type in ["qcow2", "raw", "anaconda-iso"]:
            bib_utils.create_qemu_vm(params, env, test)
        elif disk_image_type in ["ami"]:
            if len(aws_config_dict) != 0:
                bib_utils.create_and_start_cloud_vm(params)
            else:
                bib_utils.create_qemu_vm(params, env, test)
    except Exception as ex:
        raise ex
    finally:
        vm_name = params.get("vm_name_bootc")
        if vm_name and vm_name in virsh.dom_list().stdout_text:
            virsh.undefine(vm_name, options="--nvram", ignore_status=True)
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
