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
import random
import shutil
import time
import textwrap
import pathlib

from avocado.utils import path, process
from avocado.core import exceptions
from virttest import utils_package
from virttest import remote

from provider.bootc_image_builder import aws_utils

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


def podman_command_build(bib_image_url, disk_image_type, image_ref, config=None, local_container=False, tls_verify="true", chownership=None,
                         key_store_mounted=None, target_arch=None, rootfs=None, options=None, **dargs):
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
    :param key_store_mounted: whether mount keystore folder
    :param target_arch: whether specify architecture
    :param rootfs: whether specify rootfs type
    :param options: additional options if needed
    :param dargs: standardized function API keywords
    :return: CmdResult object
    """
    if not os.path.exists("/var/lib/libvirt/images/output"):
        os.makedirs("/var/lib/libvirt/images/output")
    cmd = "sudo podman run --rm -it --privileged --pull=newer --security-opt label=type:unconfined_t -v /var/lib/libvirt/images/output:/output"
    if config:
        cmd += " -v %s:/config.json  " % config

    if local_container:
        cmd += " -v /var/lib/containers/storage:/var/lib/containers/storage "

    if options is not None:
        cmd += " -v %s:/run/containers/0/auth.json " % options

    if key_store_mounted:
        cmd += " -v %s " % key_store_mounted

    if dargs.get('aws.secrets'):
        cmd += " --env-file=%s " % dargs.get('aws.secrets')

    cmd += " %s " \
        " --type %s --tls-verify=%s " % (bib_image_url, disk_image_type, tls_verify)

    if config:
        cmd += " --config /config.json "

    if target_arch:
        cmd += " --target-arch=%s " % target_arch

    aws_ami_name = dargs.get('aws_ami_name')
    if aws_ami_name:
        random_int = random.randint(1, 1000)
        vm_arch_name = dargs.get("vm_arch_name", "x86_64")
        LOG.debug(f"vm_arch_name value in podman build is : {vm_arch_name}")
        aws_ami_name = f"{aws_ami_name}_{vm_arch_name}_{random_int}"
        cmd += f" --aws-ami-name {aws_ami_name} --aws-bucket {dargs.get('aws_bucket')} --aws-region {dargs.get('aws_region')} "

    if local_container:
        cmd += " --local %s " % image_ref
    else:
        cmd += " %s " % image_ref

    if chownership:
        cmd += " --chown %s " % chownership

    if rootfs:
        cmd += " --rootfs %s " % rootfs

    debug = dargs.get("debug", True)

    ignore_status = dargs.get("ignore_status", False)
    timeout = int(dargs.get("timeout", "1800"))
    LOG.debug("the whole podman command: %s\n" % cmd)

    ret = process.run(
        cmd, timeout=timeout, verbose=debug, ignore_status=ignore_status, shell=True)

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


def podman_login_with_auth(auth_file, registry):
    """
    Use podman to login in registry with auth file

    :param auth_file: auth file
    :param registry: registry to login
    :return: CmdResult object
    """
    command = "sudo podman login --authfile=%s %s " % (auth_file, registry)
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


def create_config_json_file(params):
    """
    create json configuration file

    :param params: one dictionary to pass in configuration
    """
    folder = params.get("config_file_path")
    username = params.get("os_username")
    password = params.get("os_password")
    kickstart = "yes" == params.get("kickstart")
    public_key_path = os.path.join(os.path.expanduser("~/.ssh/"), "id_rsa.pub")
    if not os.path.exists(public_key_path):
        LOG.debug("public key doesn't exist, will help create one")
        key_gen_cmd = "ssh-keygen -q -t rsa -N '' <<< $'\ny' >/dev/null 2>&1"
        process.run(key_gen_cmd, shell=True, ignore_status=False)

    with open(public_key_path, 'r') as ssh:
        key_value = ssh.read().rstrip()
    if not kickstart:
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
                    "kernel": {"append": "mitigations=auto,nosmt"},
                },
            },
        }
    else:
        cfg = {
            "blueprint": {
                "customizations": {
                    "kernel": {"append": "mitigations=auto,nosmt"},
                    "installer": {
                        "modules": {"enable": [
                                    "org.fedoraproject.Anaconda.Modules.Localization",
                                    # disable takes precedence
                                    "org.fedoraproject.Anaconda.Modules.Timezone",
                                    ],
                                    "disable": [
                                        # disable takes precedence
                                        "org.fedoraproject.Anaconda.Modules.Timezone",
                                    ]
                                    },
                        "kickstart": {"contents": "user --name %s --password %s --groups wheel\n"
                                      "sshkey --username %s \"%s\"\ntext --non-interactive\nzerombr\n"
                                      "clearpart --all --initlabel --disklabel=gpt\nautopart --noswap --type=lvm\n"
                                      "network --bootproto=dhcp --device=link --activate --onboot=on\n reboot" % (username, password, username, key_value)
                                      }
                    }
                }
            }
        }

    LOG.debug("what is cfg:%s", cfg)
    config_json_path = pathlib.Path(folder) / "config.json"
    config_json_path.write_text(json.dumps(cfg), encoding="utf-8")
    return os.path.join(folder, "config.json")


def create_auth_json_file(params):
    """
    create authentication json configuration file

    :param params: one dictionary to pass in configuration
    """
    folder = params.get("config_file_path")
    redhat_registry = params.get("redhat_registry")
    registry_key = params.get("registry_key")

    cfg = {
        "auths": {
            "%s" % redhat_registry: {
                "auth": "%s" % registry_key
            }
        }
    }

    LOG.debug("what is auth cfg:%s", cfg)
    config_json_path = pathlib.Path(folder) / "auth.json"
    config_json_path.write_text(json.dumps(cfg), encoding="utf-8")
    final_config_file = os.path.join(folder, "auth.json")
    return final_config_file


def create_aws_secret_file(folder, aws_access_key_id, aws_access_key):
    """
    Create aws secret key file

    :param folder: folder is used to have secret file
    :param aws_access_key_id: aws access key id
    :param aws_access_key: aws access key
    """
    secret_path = pathlib.Path(folder) / "aws.secrets"
    secret_path.write_text(textwrap.dedent(f"""
    AWS_ACCESS_KEY_ID={aws_access_key_id}
    AWS_SECRET_ACCESS_KEY={aws_access_key}
    """), encoding="utf8")

    return os.path.join(folder, "aws.secrets")


def create_and_build_container_file(params):
    """
    Create container file and build container tag

    :param params: one dictionary to wrap up all parameters
    """
    folder = params.get("container_base_folder")
    build_container = params.get("build_container")
    container_tag = params.get("container_url")

    # clean up existed image
    clean_image_cmd = "sudo podman rmi %s" % container_tag
    process.run(clean_image_cmd, shell=True, ignore_status=True)
    etc_config = ''
    dnf_vmware_tool = ''
    dnf_fips_install = ''

    # create VMware tool
    if params.get("add_vmware_tool") == "yes":
        vmware_tool_path = os.path.join(folder, "etc/vmware-tools/")
        if not os.path.exists(vmware_tool_path):
            os.makedirs(vmware_tool_path)
        etc_config = "COPY etc/ /etc/"
        dnf_vmware_tool = "dnf -y install open-vm-tools && dnf clean all && systemctl enable vmtoolsd.service && "

        download_vmware_config_cmd = "curl https://gitlab.com/fedora/bootc/" \
            "examples/-/raw/main/vmware/etc/vmware-tools/tools.conf > %s/tools.conf" % vmware_tool_path
        process.run(download_vmware_config_cmd, shell=True, verbose=True, ignore_status=True)

    if params.get("fips_enable") == "yes":
        dnf_fips_install = "RUN cat > /usr/lib/bootc/kargs.d/01-fips.toml <<'EOF'\n" \
                           "kargs = ['fips=1']\n" \
                           "match-architectures = ['x86_64']\n" \
                           "EOF\n" \
                           "RUN dnf install -y crypto-policies-scripts && " \
                           "update-crypto-policies --no-reload --set FIPS "

    container_path = pathlib.Path(folder) / "Containerfile_tmp"
    shutil.copy("/etc/yum.repos.d/beaker-BaseOS.repo", folder)
    shutil.copy("/etc/yum.repos.d/beaker-AppStream.repo", folder)

    create_sudo_file = "RUN echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/wheel-passwordless-sudo"
    enable_root_ssh = "RUN echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config.d/01-permitrootlogin.conf"

    container_file_content = f"""\n
        FROM {build_container}
        {etc_config}
        COPY beaker-BaseOS.repo /etc/yum.repos.d/
        COPY beaker-AppStream.repo /etc/yum.repos.d/
        {create_sudo_file}
        {enable_root_ssh}
        {dnf_fips_install}
        RUN {dnf_vmware_tool} dnf install -y vim && dnf clean all
        """

    custom_repo = params.get("custom_repo")
    if custom_repo:
        repo_path = pathlib.Path(folder) / "rhel-9.4.repo"
        repo_prefix = "rhel-9.4"
        if "rhel-9.5" in custom_repo:
            repo_path = pathlib.Path(folder) / "rhel-9.5.repo"
            repo_prefix = "rhel-9.5"
        if "rhel-10.0" in custom_repo:
            repo_path = pathlib.Path(folder) / "rhel-10.0.repo"
            repo_prefix = "rhel-10.0"
        compose_url = params.get("compose_url")
        baseurl = get_baseurl_from_repo_file("/etc/yum.repos.d/beaker-AppStream.repo")
        if baseurl:
            compose_url = baseurl
        vm_arch_name = params.get("vm_arch_name", "x86_64")
        repo_content = f"""\n
            [{repo_prefix}-baseos]
            name=beaker-BaseOS\n
            baseurl={compose_url}/compose/BaseOS/{vm_arch_name}/os/
            enabled=1
            gpgcheck=0
            sslverify=0\n
            [{repo_prefix}-appstream]
            name=beaker-appstream\n
            baseurl={compose_url}/compose/AppStream/{vm_arch_name}/os/
            enabled=1
            gpgcheck=0
            sslverify=0\n
            """
        nfv_repo_content = f"""
            [{repo_prefix}-nfv]
            name=beaker-NFV\n
            baseurl={compose_url}/compose/NFV/{vm_arch_name}/os/
            enabled=1
            gpgcheck=0
            sslverify=0\n
            """
        crb_repo_content = f"""
            [{repo_prefix}-crb]
            name=beaker-CRB\n
            baseurl={compose_url}/compose/CRB/{vm_arch_name}/os/
            enabled=1
            gpgcheck=0
            sslverify=0\n
            """

        if "x86_64" in vm_arch_name:
            repo_content = repo_content + nfv_repo_content + crb_repo_content
        repo_path.write_text(textwrap.dedent(repo_content), encoding="utf8")
        container_file_content = f"""\n
        FROM {build_container}
        COPY {custom_repo} /etc/yum.repos.d/
        {create_sudo_file}
        {enable_root_ssh}
        {dnf_fips_install}
        Run dnf clean all
        """

    container_path.write_text(textwrap.dedent(container_file_content), encoding="utf8")
    build_cmd = "sudo podman build -t %s -f %s" % (container_tag, str(container_path))
    process.run(build_cmd, shell=True, ignore_status=False)


def create_and_start_vmware_vm(params):
    """
    prepare environment, upload vmdk, create and start vm

    @param params: one dictionary wrapping various parameter
    """
    image_type = params.get("disk_image_type")
    try:
        install_vmware_govc_tool(params)
        setup_vCenter_env(params)
        (params)
        if image_type == "vmdk":
            import_vmdk_to_vCenter(params)
        elif image_type == "anaconda-iso":
            import_iso_to_vCenter(params)
        create_vm_in_vCenter(params)
        if image_type == "vmdk":
            attach_disk_to_vm(params)
        elif image_type == "anaconda-iso":
            attach_iso_to_vm(params)
            create_vmdk_on_vm(params)

        power_on_vm(params)
        add_vmware_tool = "yes" == params.get("add_vmware_tool")
        if add_vmware_tool:
            verify_ssh_login_vm(params)
    finally:
        delete_vm_if_present(params)


def create_and_start_cloud_vm(params):
    """
    prepare environment, create and start VM in cloud

    @param params: one dictionary wrapping various parameter
    """
    try:
        aws_utils.create_aws_instance(params)
        verify_ssh_login_vm(params)
    finally:
        cleanup_aws_env(params)


def install_vmware_govc_tool(params):
    """
    Download VmWare govc tool and install it

    :param params: wrap up test parameters
    """
    vm_arch_name = params.get("vm_arch_name", "x86_64")
    if "arm64" in vm_arch_name:
        vm_arch_name = "arm64"
    govc_install_cmd = f"curl -L -o - 'https://github.com/vmware/govmomi/releases/latest/download/govc_Linux_{vm_arch_name}.tar.gz' " \
        f"| tar -C /usr/local/bin -xvzf - govc"
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
    os.environ["GOVC_DATASTORE"] = "%s" % params.get("DATA_STORE")
    os.environ["GOVC_INSECURE"] = "true"
    process.run("govc about", shell=True, ignore_status=False)


def import_vmdk_to_vCenter(params):
    """
    import vmdk into vCenter

    @param params: one dictionary wrapping various parameter
    """
    delete_datastore_if_existed(params)
    import_cmd = f"govc import.vmdk -force=true {params.get('vm_disk_image_path')}"
    process.run(import_cmd, shell=True, verbose=True, ignore_status=False)


def create_vmdk_on_vm(params):
    """
    create empty vmdk on VM

    @param params: one dictionary wrapping various parameters
    """
    vm_name = params.get("vm_name_bootc")
    create_vmdk_cmd = f"govc vm.disk.create -vm {vm_name} -name {vm_name}.vmdk 10G"
    process.run(create_vmdk_cmd, shell=True, verbose=True, ignore_status=False)


def import_iso_to_vCenter(params):
    """
    import iso into vCenter

    @param params: one dictionary wrapping various parameter
    """
    boot = params.get("vm_name_bootc")
    iso_location = params.get("vm_disk_image_path")
    check_iso_cmd = f"govc datastore.ls {boot}"
    if process.run(check_iso_cmd, shell=True, verbose=True, ignore_status=True).exit_status == 0:
        delete_iso_cmd = f"govc datastore.rm -f  {boot}"
        process.run(delete_iso_cmd, shell=True, verbose=True, ignore_status=False)

    import_iso_cmd = f"govc datastore.upload {iso_location} {boot}"
    process.run(import_iso_cmd, shell=True, verbose=True, ignore_status=False)


def create_vm_in_vCenter(params):
    """
    create VM in vCenter

    @param params: one dictionary wrapping various parameter
    """
    create_cmd = "govc vm.create -net='VM Network'  -on=false -c=2 " \
        "-m=4096 -g=centos9_64Guest -firmware=%s %s" % (params.get("firmware"), params.get("vm_name_bootc"))
    process.run(create_cmd, shell=True, verbose=True, ignore_status=False)


def attach_disk_to_vm(params):
    """
    attach disk to VM in vCenter

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    attach_cmd = "govc vm.disk.attach -vm %s" \
        " -controller %s -link=false -disk=%s/%s.vmdk" % (vm_name, params.get("controller"), vm_name, vm_name)
    process.run(attach_cmd, shell=True, verbose=True, ignore_status=False)


def attach_iso_to_vm(params):
    """
    attach ISO to VM in vCenter

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    add_cdrom_cmd = f"govc device.cdrom.add -vm {vm_name}"
    id = process.run(add_cdrom_cmd, shell=True, verbose=True, ignore_status=False).stdout_text.strip()

    #insert ISO into CDROM
    attach_cmd = f"govc device.cdrom.insert -vm {vm_name} -device {id} {vm_name}"
    process.run(attach_cmd, shell=True, verbose=True, ignore_status=False)


def power_on_vm(params):
    """
    power on VM in vCenter

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    wait_boot_time = int(params.get("wait_boot_time", "40"))
    power_on_cmd = "govc vm.power -on=true %s" % vm_name
    process.run(power_on_cmd, shell=True, verbose=True, ignore_status=False)
    time.sleep(wait_boot_time)
    state_cmd = "govc vm.info -json %s |jq -r .virtualMachines[0].summary.runtime.powerState" % vm_name
    result = process.run(state_cmd, shell=True, verbose=True, ignore_status=False).stdout_text.strip()
    if result not in "poweredOn":
        raise exceptions.TestFail(f"The VM state is not powered on, real state is: {result}")


def verify_ssh_login_vm(params):
    """
    Verify ssh login VM successfully

    @param params: one dictionary wrapping various parameter
    """
    ip_address = params.get("ip_address")
    disk_image_type = params.get("disk_image_type")
    aws_config_dict = eval(params.get("aws_config_dict", '{}'))
    if ip_address is None:
        if disk_image_type in ['ami'] and len(aws_config_dict) != 0:
            ip_address = aws_utils.get_aws_instance_privateip(params)
        else:
            ip_address = get_vm_ip_address(params)
    user = params.get("os_username")
    passwd = params.get("os_password")
    vm_params = {}
    vm_params.update({"server_ip": ip_address})
    vm_params.update({"server_user": user})
    vm_params.update({"server_pwd": passwd})
    vm_params.update({"vm_ip": ip_address})
    vm_params.update({"vm_user": user})
    vm_params.update({"vm_pwd": passwd})
    remote_vm_obj = remote.VMManager(vm_params)
    remote_vm_obj.check_network()
    remote_vm_obj.setup_ssh_auth()
    result = remote_vm_obj.cmd_status_output("whoami")[1].strip()
    LOG.debug(f" remote VM test is: {result} ")
    if result not in user:
        raise exceptions.TestFail(f"The expected user name should be: {user}, but actually is: {result}")
    return remote_vm_obj


def get_vm_ip_address(params):
    """
    Get VM ip_address in vCenter

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    get_ip_cmd = "govc vm.ip -v4  -wait=3m  %s" % vm_name
    result = process.run(get_ip_cmd, shell=True, verbose=True, ignore_status=False)
    LOG.debug("result wcf: {result.stdout_text}")
    if result.stdout_text.strip() == "" or result.stdout_text.strip() is None:
        raise exceptions.TestFail(f"Can not get ip address")
    return result.stdout_text.strip()


def delete_vm_if_present(params):
    """
    delete vm if present

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    find_cmd = "govc find / -type m -name %s" % vm_name
    cmd_result = process.run(find_cmd, shell=True, verbose=True, ignore_status=True).stdout_text
    LOG.debug(f"find vm in vsphere is:{cmd_result}")
    if cmd_result:
        vm_path = cmd_result.strip()
        delete_cmd = "govc vm.destroy %s && govc datastore.rm -f %s" % (vm_name, vm_name)
        process.run(delete_cmd, shell=True, verbose=True, ignore_status=True)


def delete_datastore_if_existed(params):
    """
    delete data store if existed

    @param params: one dictionary wrapping various parameter
    """
    vm_name = params.get("vm_name_bootc")
    find_cmd = "govc datastore.ls %s" % vm_name
    cmd_result = process.run(find_cmd, shell=True, verbose=True, ignore_status=True).stdout_text
    if cmd_result:
        delete_cmd = "govc datastore.rm -f %s" % vm_name
        process.run(delete_cmd, shell=True, verbose=True, ignore_status=True)


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


def set_root_passwd(remote_vm_obj, params):
    """
    Use passwd to change root password

    @param remote_vm_obj: remote vm object
    @param params: one dictionary containing parameters
    """
    disk_image_type = params.get("disk_image_type")
    root_passwd = params.get("root_passwd", "redhat")
    fips_enable = params.get("fips_enable") == "yes"
    if fips_enable:
        change_passwd = remote_vm_obj.cmd_status_output("echo root:%s|sudo chpasswd" % root_passwd)[1].strip()
        LOG.debug(f"change remote VM root password is: {change_passwd} ")
        remote_vm_obj = None


def virt_install_vm(params):
    """
    Use virt install tool to install vm

    @param params: one dictionary containing parameters
    """
    vm_name = params.get("vm_name_bootc")
    disk_image_type = params.get("disk_image_type")
    image_ref = params.get("image_ref")
    disk_path = params.get("vm_disk_image_path")
    iso_install_path = params.get("iso_install_path")
    format = params.get("disk_image_type")
    firmware = params.get("firmware")
    ovmf_code_path = params.get("ovmf_code_path")
    ovmf_vars_path = params.get("ovmf_vars_path")
    boot_option = ''
    vm_arch_name = params.get("vm_arch_name", "x86_64")
    machine_type = " --machine q35 "
    secure_boot_feature0_enable = "yes"
    if "aarch64" in vm_arch_name:
        machine_type = ""
        secure_boot_feature0_enable = "no"
    if firmware in ['efi']:
        if image_ref in ["centos", "fedora"]:
            boot_option = f"--boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled={secure_boot_feature0_enable}," \
                f"firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=no"
        else:
            boot_option = " --boot uefi"
    if disk_image_type in ["anaconda-iso"]:
        if os.path.exists(iso_install_path):
            os.remove(iso_install_path)
        cmd = ("virt-install --name %s"
               " --disk path=%s,bus=virtio,format=qcow2,size=12"
               " --vcpus 3 --memory 3096"
               " --osinfo detect=on,require=off"
               " --graphics vnc"
               " --video virtio"
               " --serial pty"
               " --wait 10"
               " --cdrom %s"
               " --debug"
               " %s "
               " %s "
               " --noreboot" %
               (vm_name, iso_install_path, disk_path, machine_type, boot_option))
    else:
        cmd = ("virt-install --name %s"
               " --disk path=%s,bus=virtio,format=%s"
               " --import "
               " --vcpus 3 --memory 3096"
               " --osinfo detect=on,require=off"
               " --graphics vnc --video virtio --noautoconsole --serial pty"
               " --wait 10"
               " --debug"
               " %s "
               " %s "
               " --noreboot" %
               (vm_name, disk_path, format, machine_type, boot_option))
    process.run(cmd, shell=True, verbose=True, ignore_status=True)


def verify_in_vm_internal(vm, params):
    """
    Verify something by login Vm

    @param vm: vm object
    @param params: one dictionary wrapping various parameter
    """
    root_user = "root"
    root_passwd = params.get("root_passwd", "redhat")
    fips_enable = params.get("fips_enable") == "yes"
    disk_image_type = params.get("disk_image_type")
    vm_arch_name = params.get("vm_arch_name", "x86_64")
    if fips_enable and "x86_64" in vm_arch_name and disk_image_type not in ["anaconda-iso"]:
        vm_session = vm.wait_for_login(username=root_user, password=root_passwd)
        cmd = "fips-mode-setup --check"
        res = vm_session.cmd_output(cmd)
        vm_session.close()
        LOG.debug('Session outputs:\n%s', res)
        if "FIPS mode is enabled" not in res:
            raise exceptions.TestFail(f"FIPS mode is not enabled")


def create_qemu_vm(params, env, test):
    """
    prepare environment, virt install, and login vm

    @param params: one dictionary wrapping various parameters
    @param env: environment
    @param test: test case itself
    """
    try:
        virt_install_vm(params)
        vm_name = params.get("vm_name_bootc")
        env.create_vm(vm_type='libvirt', target=None, name=vm_name, params=params, bindir=test.bindir)
        vm = env.get_vm(vm_name)
        if vm.is_dead():
            LOG.debug("VM is dead, starting")
            vm.start()
        ip_address = vm.wait_for_get_address(nic_index=0)
        params.update({"ip_address": ip_address.strip()})
        remote_vm_obj = verify_ssh_login_vm(params)
        LOG.debug(f"ip addressis wcf: {ip_address}")
        set_root_passwd(remote_vm_obj, params)
        verify_in_vm_internal(vm, params)
    finally:
        if vm and vm.is_alive():
            vm.destroy(gracefully=False)
            vm.undefine(options='--nvram')


def get_group_and_user_ids(folder_path):
    try:
        stat_info = os.stat(folder_path)
        gid = stat_info.st_gid
        uid = stat_info.st_uid
        return gid, uid
    except FileNotFoundError:
        LOG.debug(f"Folder '{folder_path}' not found.")
        return None, None
    except Exception as ex:
        LOG.debug(f"Error occurred: {ex}")
        return None, None


def prepare_aws_env(params):
    """
    One method to prepare AWS environment for image build

    :param params: one collective object representing wrapped parameters
    """
    aws_access_key_id = params.get("aws_access_key_id")
    aws_access_key = params.get("aws_access_key")
    aws_secret_folder = params.get("aws_secret_folder")
    aws_region = params.get("aws_region")
    create_aws_secret_file(aws_secret_folder, aws_access_key_id, aws_access_key)
    aws_utils.create_aws_credentials_file(aws_access_key_id, aws_access_key)
    aws_utils.create_aws_config_file(aws_region)
    aws_utils.install_aws_cli_tool(params)


def cleanup_aws_env(params):
    """
    One method to clean up AWS environment for image build

    :param params: one collective object representing wrapped parameters
    """
    aws_utils.terminate_aws_instance(params)
    aws_utils.delete_aws_ami_id(params)
    aws_utils.delete_aws_ami_snapshot_id(params)


def cleanup_aws_ami_and_snapshot(params):
    """
    One method to clean up AWS ami and snapshot for image build

    :param params: one collective object representing wrapped parameters
    """
    aws_utils.delete_aws_ami_id(params)
    aws_utils.delete_aws_ami_snapshot_id(params)


def get_baseurl_from_repo_file(repo_file_path):
    """
    One method to get compose url from current repository file

    :param repo_file_path: file path to repository
    """
    try:
        with open(repo_file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith("baseurl"):
                    baseurl = line.split("=")[1].strip()
                    return baseurl.split('/compose/')[0].strip()
        return None
    except FileNotFoundError:
        return None
