"""VM name patterns and connection defaults for v2v tests.

The first matching ESX profile is applied by utils_v2v.set_defaults.
"""

FIRSTBOOT_TIMEOUT_LINUX = 600
FIRSTBOOT_TIMEOUT_WINDOWS = 2400

esx_70_vmname_regex = r".*esx7\.0.*"
esx_80_vmname_regex = r".*esx8\.0.*"
esx_90_vmname_regex = r".*esx9\.0.*"

esx_70 = {
    "datastore": "VPX_70_DATASTORE_V2V_EXAMPLE",
    "vpx_hostname": "VPX_70_HOSTNAME_V2V_EXAMPLE",
    "vpx_dc": "VPX_70_DC_V2V_EXAMPLE",
    "esx_hostname": "ESX_70_HOSTNAME_V2V_EXAMPLE",
    "vpx_password": "VPX_70_PASSWORD_V2V_EXAMPLE",
    "esxi_password": "ESX_70_PASSWORD_V2V_EXAMPLE",
    "vddk_libdir_src": "MOUNT_SRC_VDDK70_LIB_DIR_V2V_EXAMPLE",
}

esx_80 = {
    "datastore": "VPX_80_DATASTORE_V2V_EXAMPLE",
    "vpx_hostname": "VPX_80_HOSTNAME_V2V_EXAMPLE",
    "vpx_dc": "VPX_80_DC_V2V_EXAMPLE",
    "esx_hostname": "ESX_80_HOSTNAME_V2V_EXAMPLE",
    "vpx_password": "VPX_80_PASSWORD_V2V_EXAMPLE",
    "esxi_password": "ESX_80_PASSWORD_V2V_EXAMPLE",
    "vddk_libdir_src": "MOUNT_SRC_VDDK80_LIB_DIR_V2V_EXAMPLE",
}

esx_90 = {
    "datastore": "VPX_90_DATASTORE_V2V_EXAMPLE",
    "vpx_hostname": "VPX_90_HOSTNAME_V2V_EXAMPLE",
    "vpx_dc": "VPX_90_DC_V2V_EXAMPLE",
    "esx_hostname": "ESX_90_HOSTNAME_V2V_EXAMPLE",
    "vpx_password": "VPX_90_PASSWORD_V2V_EXAMPLE",
    "esxi_password": "ESX_90_PASSWORD_V2V_EXAMPLE",
    "vddk_libdir_src": "MOUNT_SRC_VDDK90_LIB_DIR_V2V_EXAMPLE",
}
