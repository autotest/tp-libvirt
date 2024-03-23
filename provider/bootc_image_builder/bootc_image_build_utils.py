# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: chwen@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""Helper functions for bootc image builder"""

import logging
import json
import os
import shutil
import textwrap
import pathlib

from avocado.utils import path, process
from virttest import utils_package

LOG = logging.getLogger('avocado.' + __name__)


def install_bib_packages():
    """
    install necessary bootc image builder necessary packages

    """
    package_list = ["podman", "skopeo", "virt-install", "curl", "virt-manager"]
    for pkg in package_list:
        try:
            path.find_command(pkg)
        except path.CmdNotFoundError:
            utils_package.package_install(pkg)


def podman_command_build(bib_image_url, disk_image_type, image_ref, config=None, local_container=False, tls_verify="true", chownership=None, options=None, **dargs):
    """
    Use podman run command to launch bootc image builder

    :param bib_image_url: bootc image builder url
    :param disk_image_type: image type to build [qcow2, ami] (default "qcow2")
    :param image_ref: image reference
    :param config: config file
    :param local_container: whether use local container image
    :param tls_verify: whether verify tls connection
    :param local_container: whether use local container image
    :param chownership: whether change output ownership
    :param options: additional options if needed
    :param dargs: standardized virsh function API keywords
    :return: CmdResult object
    """
    if not os.path.exists("/var/lib/libvirt/images/output"):
        os.makedirs("/var/lib/libvirt/images/output")
    cmd = "sudo podman run --rm -it --privileged --pull=newer --security-opt label=type:unconfined_t -v /var/lib/libvirt/images/output:/output"
    if config:
        cmd += " -v %s:/config.json  " % config

    if local_container:
        cmd += " -v /var/lib/containers/storage:/var/lib/containers/storage "

    cmd += " %s " \
        " --type %s --tls-verify=%s " % (bib_image_url, disk_image_type, tls_verify)

    if config:
        cmd += " --config /config.json "

    if local_container:
        cmd += " --local %s " % image_ref
    else:
        cmd += " %s " % image_ref

    if chownership:
        cmd += " --chown %s " % chownership

    if options is not None:
        cmd += " %s" % options

    debug = dargs.get("debug", True)

    ignore_status = dargs.get("ignore_status", False)
    timeout = int(dargs.get("timeout", "1800"))
    LOG.debug("the whole podman command: %s\n" % cmd)

    ret = process.run(
        cmd, timeout=timeout, verbose=debug, ignore_status=ignore_status, shell=True)

    ret.stdout = ret.stdout_text
    ret.stderr = ret.stderr_text
    return ret


def podman_login(podman_username, podman_password, registry):
    """
    Use podman to login in registry

    :param podman_username: podman username
    :param podman_password: podman password
    :param registry: registry to login
    :return: CmdResult object
    """
    command = "sudo podman login -u='%s' -p='%s' %s " % (podman_username, podman_password, registry)
    process.run(
        command, timeout=60, verbose=True, ignore_status=False, shell=True)


def podman_push(podman_username, podman_password, registry, container_url):
    """
    Use podman image to registry

    :param podman_username: podman username
    :param podman_password: podman password
    :param registry: registry to login
    :param container_url: image url
    :return: CmdResult object
    """
    podman_login(podman_username, podman_password, registry)
    command = "sudo podman push %s " % container_url
    process.run(
        command, timeout=1200, verbose=True, ignore_status=False, shell=True)


def create_config_json_file(folder, username, password):
    """
    install necessary bootc image builder necessary packages

    :param folder: the folder that config.json reside in
    :param username: user name
    :param password: user password
    """
    public_key_path = os.path.join(os.path.expanduser("~/.ssh/"), "id_rsa.pub")
    if not os.path.exists(public_key_path):
        LOG.debug("public key doesn't exist, please create one")
        key_gen_cmd = "ssh-keygen -q -t rsa -N '' <<< $'\ny' >/dev/null 2>&1"
        process.run(key_gen_cmd, shell=True, ignore_status=False)

    with open(public_key_path, 'r') as ssh:
        key_value = ssh.read().rstrip()
    cfg = {
        "blueprint": {
            "customizations": {
                "user": [
                    {
                        "name": username,
                        "password": password,
                        "groups": ["wheel"],
                        "key": "%s" % key_value,
                    },
                ],
            },
        },
    }
    LOG.debug("what is cfg:%s", cfg)
    config_json_path = pathlib.Path(folder) / "config.json"
    config_json_path.write_text(json.dumps(cfg), encoding="utf-8")
    return os.path.join(folder, "config.json")


def create_and_build_container_file(folder, build_container, container_tag):
    """
    Create container file and build container tag

    :param folder: the folder that config.json reside in
    :param build_container: the base container image
    :param container_tag: container tag
    """
    # clean up existed image
    clean_image_cmd = "sudo podman rmi %s" % container_tag
    process.run(clean_image_cmd, shell=True, ignore_status=True)

    container_path = pathlib.Path(folder) / "Containerfile_tmp"
    shutil.copy("/etc/yum.repos.d/beaker-BaseOS.repo", folder)
    shutil.copy("/etc/yum.repos.d/beaker-AppStream.repo", folder)
    container_path.write_text(textwrap.dedent(f"""\n
    FROM {build_container}
    COPY beaker-BaseOS.repo /etc/yum.repos.d/
    COPY beaker-AppStream.repo /etc/yum.repos.d/
    RUN dnf install -y vim && dnf clean all
    """), encoding="utf8")
    build_cmd = "sudo podman build -t %s -f %s" % (container_tag, str(container_path))
    process.run(build_cmd, shell=True, ignore_status=False)


def install_vmware_govc_tool():
    """
    Download VmWare govc tool and install it

    """
    govc_install_cmd = "curl -L -o - 'https://github.com/vmware/govmomi/releases/latest/download/govc_Linux_x86_64.tar.gz' " \
        "| tar -C /usr/local/bin -xvzf - govc"
    print(govc_install_cmd)
    if not os.path.exists("/usr/local/bin/govc"):
        process.run(govc_install_cmd, shell=True, ignore_status=False)


def setup_vCenter_env(params):
    """
    Download VmWare govc tool and install it

    @param params: one dictionary wrapping various parameter
    """
    # vCenter information
    os.environ["GOVC_URL"] = params.get("GOVC_URL")
    os.environ["GOVC_USERNAME"] = params.get("GOVC_USERNAME")
    os.environ["GOVC_PASSWORD"] = params.get("GOVC_PASSWORD")
    os.environ["DATA_CENTER"] = params.get("DATA_CENTER")
    os.environ["DATA_STORE"] = params.get("DATA_STORE")
    os.environ["GOVC_INSECURE"] = "true"
    process.run("govc about", shell=True, ignore_status=False)


def parse_container_url(params):
    """
    Parse repository information from container url

    @param params: wrapped dictionary containing url
    """
    container_url = params.get("container_url")
    repository_info = container_url.split('/')[-1]
    repository_name = repository_info.split(':')[0]
    if "localhost" in container_url:
        repository_name = "localhost-%s" % repository_name
    return repository_name


def convert_disk_image_name(params):
    """
    Convert disk type image name

    @param params: wrapped dictionary containing parameters
    """
    repository_name = parse_container_url(params)
    origin_disk_name, extension = os.path.splitext(params.get("output_name"))
    dest_disk_name = "%s-%s%s" % (origin_disk_name, repository_name, extension)
    return dest_disk_name
