# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: chwen@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""Helper functions for aws management"""

import logging
import os
import shutil
import textwrap
import pathlib

from avocado.utils import process
from avocado.core import exceptions

LOG = logging.getLogger('avocado.' + __name__)


def install_aws_cli_tool(params):
    """
    Download AWS command line tool and install it

    :param params: wrap up all parameters
    """
    vm_arch_name = params.get("vm_arch_name", "x86_64")
    aws_install_cmd = f"curl https://awscli.amazonaws.com/awscli-exe-linux-{vm_arch_name}.zip -o awscliv2.zip " \
        f" && unzip awscliv2.zip -d ./ && ./aws/install "
    if not os.path.exists("/usr/local/bin/aws"):
        if os.path.exists("/usr/local/aws-cli"):
            shutil.rmtree("/usr/local/aws-cli")
        if os.path.exists("/usr/local/bin/aws_completer"):
            shutil.rmtree("/usr/local/bin/aws_completer")
        if os.path.exists("aws"):
            shutil.rmtree("aws")
        if os.path.exists("awscliv2.zip"):
            os.remove("awscliv2.zip")
        process.run(aws_install_cmd, shell=True, ignore_status=False)


def create_aws_credentials_file(aws_access_key_id, aws_access_key):
    """
    Create AWS credentials file

    :param aws_access_key_id: AWS access key id
    :param aws_access_key: AWS access key
    """
    folder = os.path.expanduser("~/.aws")
    if not os.path.exists(folder):
        os.mkdir(folder)
    secret_path = pathlib.Path(folder) / "credentials"
    secret_path.write_text(textwrap.dedent(f"""
    [default]
    AWS_ACCESS_KEY_ID={aws_access_key_id}
    AWS_SECRET_ACCESS_KEY={aws_access_key}
    """), encoding="utf8")

    return os.path.join(folder, "credentials")


def create_aws_config_file(aws_region):
    """
    Create AWS configuration file

    :param aws_region: AWS region
    """
    folder = os.path.expanduser("~/.aws")
    if not os.path.exists(folder):
        os.mkdir(folder)
    secret_path = pathlib.Path(folder) / "config"
    secret_path.write_text(textwrap.dedent(f"""
    [default]
    region={aws_region}
    """), encoding="utf8")

    return os.path.join(folder, "config")


def delete_aws_ami_id(params):
    """
    delete AWS AMI id

    @param params: one dictionary wrapping various parameter
    """
    ami_id = params.get("aws_ami_id")
    if ami_id:
        delete_cmd = "aws ec2 deregister-image --image-id %s" % ami_id
        process.run(delete_cmd, shell=True, verbose=True, ignore_status=True)


def delete_aws_ami_snapshot_id(params):
    """
    delete AWS AMI snapshot id

    @param params: one dictionary wrapping various parameter
    """
    aws_ami_snapshot_id = params.get("aws_ami_snapshot_id")
    if aws_ami_snapshot_id:
        delete_cmd = "aws ec2 delete-snapshot --snapshot-id %s" % aws_ami_snapshot_id
        process.run(delete_cmd, shell=True, verbose=True, ignore_status=True)


def delete_aws_key_pair(params):
    """
    delete AWS key pair

    @param params: one dictionary wrapping various parameter
    """
    aws_key_name = params.get("aws_key_name")
    delete_key_pair_cmd = f"aws ec2 delete-key-pair --key-name {aws_key_name}"
    process.run(delete_key_pair_cmd, shell=True, ignore_status=True).exit_status


def import_aws_key_pair(params):
    """
    Import key into AWS key pair

    @param params: one dictionary wrapping various parameter
    """
    aws_key_name = params.get("aws_key_name")
    check_key_pair_cmd = f"aws ec2 describe-key-pairs --key-name {aws_key_name}"
    status = process.run(check_key_pair_cmd, shell=True, ignore_status=True).exit_status
    if status != 0:
        public_key_path = os.path.join(os.path.expanduser("~/.ssh/"), "id_rsa.pub")
        import_cmd = "aws ec2 import-key-pair --key-name {aws_key_name}--public-key-material fileb://{public_key_path}"
        process.run(import_cmd, shell=True, verbose=True, ignore_status=True)


def create_aws_instance(params):
    """
    create AWS instance

    @param params: one dictionary wrapping various parameter
    """
    import_aws_key_pair(params)
    vm_name = params.get("vm_name_bootc")
    aws_key_name = params.get("aws_key_name")
    aws_ami_id = params.get("aws_ami_id")
    aws_subnet_id = params.get("aws_subnet_id")
    aws_security_group = params.get("aws_security_group")
    aws_instance_type = params.get("aws_instance_type")
    create_aws_instance_cmd = "aws ec2 run-instances --image-id %s --count 1" \
        " --security-group-ids %s" \
        " --instance-type %s --key-name %s --subnet-id %s" \
        " --associate-public-ip-address --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=%s}]'" \
        " --block-device-mappings '[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":20,\"VolumeType\":\"gp2\"}}]'" \
        " --query 'Instances[0].InstanceId' --output text" % (aws_ami_id, aws_security_group, aws_instance_type, aws_key_name, aws_subnet_id, vm_name)
    instance_id = process.run(create_aws_instance_cmd, shell=True, ignore_status=True).stdout_text.strip()
    params.update({"aws_instance_id": "%s" % instance_id})
    return instance_id


def wait_aws_instance_running(params):
    """
    wait for AWS instance running

    @param params: one dictionary wrapping various parameter
    """
    aws_instance_id = params.get("aws_instance_id")
    if aws_instance_id:
        wait_aws_instance_cmd = f"timeout 30 aws ec2 wait instance-running --instance-ids {aws_instance_id}"
        process.run(wait_aws_instance_cmd, shell=True, ignore_status=True)


def get_aws_instance_privateip(params):
    """
    Get AWS instance private ip

    @param params: one dictionary wrapping various parameter
    """
    wait_aws_instance_running(params)
    aws_instance_id = params.get("aws_instance_id")
    if aws_instance_id:
        get_aws_instance_privateip_cmd = f"aws ec2 describe-instances --instance-ids {aws_instance_id}" \
            f" --query 'Reservations[*].Instances[*].PrivateIpAddress' --output text"
        private_ip = process.run(get_aws_instance_privateip_cmd, shell=True, ignore_status=True).stdout_text.strip()
        return private_ip
    else:
        raise exceptions.TestFail(f"AWS instance not existed yet")


def terminate_aws_instance(params):
    """
    terminate AWS instance

    @param params: one dictionary wrapping various parameter
    """
    aws_instance_id = params.get("aws_instance_id")
    if aws_instance_id:
        terminate_aws_instance_cmd = f"aws ec2 terminate-instances --instance-ids {aws_instance_id}"
        process.run(terminate_aws_instance_cmd, shell=True, ignore_status=True)
        wait_instance_terminated = f"timeout 20 aws ec2 wait instance-terminated --instance-ids {aws_instance_id}"
        process.run(wait_instance_terminated, shell=True, ignore_status=True)
