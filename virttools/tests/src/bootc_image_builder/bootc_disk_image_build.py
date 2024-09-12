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

from avocado.utils import distro
from provider.bootc_image_builder import bootc_image_build_utils as bib_utils

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def validate_bib_output(params, test):
    """
    Common method to check whether image build output exists

    :param params: class params representing the test parameters
    :param test: test object
    """
    base_folder = params.get("output_base_folder")
    output_sub_folder = params.get("output_sub_folder")
    output_name = params.get("output_name")
    ownership = params.get("ownership")
    full_path = os.path.join(base_folder, output_sub_folder, output_name)
    if not os.path.exists(full_path):
        test.fail("bootc image build fail to generate outputs for image type: %s" % params.get("disk_image_type"))
    if ownership:
        formatted_group_user = ':'.join([f"{item}" for item in bib_utils.get_group_and_user_ids(base_folder)])
        if formatted_group_user != ownership:
            test.fail(f"The output folder:{base_folder} has wrong setting in group and user ids: {formatted_group_user}")


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

    enable_tls_verify = params.get("enable_tls_verify")
    config_json = params.get("config_json")
    config_json_file = None

    ownership = params.get("ownership")
    key_store_mounted = params.get("key_store_mounted")
    target_arch = params.get("target_arch")
    roofs = params.get("roofs")

    aws_config_dict = eval(params.get("aws_config_dict", '{}'))
    options = None

    if image_ref in ['cross_build'] and distro.detect().name in ['rhel']:
        test.cancel("rhel doesn't support cross build, it is supported on fedora only")

    bib_utils.install_bib_packages()
    if config_json == "use_config_json":
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
        bib_utils.podman_push(params.get("podman_quay_username"), params.get("podman_quay_password"),
                              params.get("registry"), container_url)

    result = bib_utils.podman_command_build(bib_image_url, disk_image_type, container_url, config_json_file,
                                            local_container, enable_tls_verify, ownership,
                                            key_store_mounted, target_arch, roofs, options, **aws_config_dict)
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
        bib_utils.cleanup_aws_ami_and_snapshot(params)


def run(test, params, env):
    """
    Test boot container image builder.
    Add existing BOOTABLE image to PODMAN (cover RHEL, FEDORA)
    Build image from BOOTABLE image with PODMAAN (AWS, QCOW2,RAW, ISO).
    Validate PODMAN build image command output.
    """
    try:
        prepare_env_and_execute_bib(params, test)
        # validate build output
        validate_bib_output(params, test)
    except Exception as ex:
        raise ex
    finally:
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
