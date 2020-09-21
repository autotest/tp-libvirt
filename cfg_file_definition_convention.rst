Cartesian configuration files(.cfg) will define test case names.
"How do cfg files work? Please refer to https://avocado-vt.readthedocs.io/en/latest/CartesianConfig.html
In order to provide consistent, human-readable, maintainable test case names, it is necessary to 
apply some conventions rules to Cartesian Configuration format.

1. Variant name is joined by underscore _ ,for example:
   variants:
       - hot_plug:
           key1 = xx
       - cold_plug:
           key2 = xx

2. Each variant defines a single dimension array with similar provision methods or operations, for example:
   variants:
       - block_test:
           backend_storage_type = "iscsi"
       - gluster_test:
           backend_storage_type = "gluster"
       - ceph_test:
           backend_storage_type = "ceph"
       - nfs_test:
           backend_storage_type = "nfs"
       - dir_pool_test:
           backend_storage_type = "dir"
   
3. Variants may be nested within other variants, the most left (outside) is used to represents higher level
   operations or options, and the most left has shorter name, for example:
   variants:
       - enable_auth:
           enable_auth = "yes"
           variants:
               - use_auth_uuid:
                   use_auth_uuid = "yes"
               - use_auth_usage:
                   use_auth_usage = "yes"


4. Variant relationship will define case name order. Variant precedence is evaluated in top-down. In other words, 
   the last parsed variants has precedence over earlier definitions,therefore the lowest variant name will be placed at the beginning
   of whole test case name. The higher level one variant represents, the lower its position should be placed in. for example:
   variants:
       - device_disk:
       - device_volume
   variants:
       - persistent
       - config
       - live
       - current
   variants:
       - cold_plug:
       - hot_plug:
   variants:
       -positive_test:
           status_error = "no"
       -negative_test:
           status_error = "yes"

5. Generally speaking,a good tip to define how many or what variants there should be is to check virsh command usage, where test cases may touch.
   for example, by execute virsh attach-device --help command, and the output shows at least it should cover four types options: 
     --persistent --config  --live --current.
   So it is very natural to define one variant to represent those 4 options

6. In order to avoid large matrix cases due to cartesian, one type flat structure is also recommended:
   - positive_test:
         variants:
            - test_1:
            - test_2:
   - negative_test:
         variants:
            - n_test_1:
            - n_test_2:
7. Value Substitutions:
   For some reusable parameter value, suggest define one variable,and use it by ${variable}
   - virtio_disks_duplicate_wwn_disk_attach_by_disk:
        .....
        disk_attach_option = "--live --targetbus scsi --wwn 0x5000c50015ea71aa --driver qemu --type disk"
        disks_attach_option = "${disk_attach_option};${disk_attach_option}"
        .....
8. Conditional judgement,use conditional judgement to change your value,and the conditional judegement can be variant value, machine type, case name,etc
   - virtio_disks_duplicate_wwn_disk:
       ...
        coldplug:  
                test_disk_option_cmd = "yes"     
