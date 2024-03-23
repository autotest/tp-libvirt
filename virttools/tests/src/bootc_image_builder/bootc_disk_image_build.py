# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen <chwen@redhat.com>
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging
import os

from provider.bootc_image_builder import bootc_image_build_utils as bib_utils

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def validate_bib_output(params, test):
    """
    Common method to check whether image build output exists

    :param params: one collective object representing wrapped parameters
    :param test: test object
    """
    base_folder = params.get("output_base_folder")
    output_sub_folder = params.get("output_sub_folder")
    output_name = params.get("output_name")
    full_path = os.path.join(base_folder, output_sub_folder, output_name)
    if not os.path.exists(full_path):
        test.fail("bootc image build fail to generate outputs for image type: %s" % params.get("disk_image_type"))


def run(test, params, env):
    """
    Test boot container image builder.
    """
    disk_image_type = params.get("disk_image_type")
    bib_image_url = params.get("bib_image_url", "quay.io/centos-bootc/bootc-image-builder:latest")
    image_ref = params.get("image_ref")
    container_url = params.get("container_url")
    local_container = "yes" == params.get("local_container")
    build_container = params.get("build_container")

    enable_tls_verify = params.get("enable_tls_verify")
    config_json = params.get("config_json")
    config_json_file = None

    ownership = params.get("ownership")

    try:
        bib_utils.install_bib_packages()
        if config_json == "use_config_json":
            config_json_file = bib_utils.create_config_json_file(params.get("config_file_path"),
                                                                 params.get("os_username"), params.get("os_password"))
        # pull base image and build local image after change
        if build_container:
            if image_ref == "rhel":
                bib_utils.podman_login(params.get("podman_redhat_username"), params.get("podman_redhat_password"),
                                       params.get("redhat_registry"))
            bib_utils.create_and_build_container_file(params.get("container_base_folder"),
                                                      build_container, container_url)
        if image_ref == "rhel":
            bib_utils.podman_push(params.get("podman_quay_username"), params.get("podman_quay_password"),
                                  params.get("registry"), container_url)

        bib_utils.podman_command_build(bib_image_url, disk_image_type, container_url, config_json_file,
                                       local_container, enable_tls_verify, ownership, options=None)
        # validate build output
        validate_bib_output(params, test)
        if disk_image_type == "vmdk":
            bib_utils.install_vmware_govc_tool()
            bib_utils.setup_vCenter_env(params)
    except Exception as ex:
        raise ex
    finally:
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
