===================================
Libvirt test provider for virt-test
===================================

This is the official test provider [1]_ for the following
subtest types:

* Libvirt
* LVSB
* V2V
* Libguestfs

Really quick start guide
------------------------

1. Fork this repo on github
2. Create a new topic branch for your work
3. Create a new test provider file in your virt test repo,
   like::

    cp io-github-autotest-libvirt.ini myprovider.ini

   ::

    [provider]
    uri: file:///home/foo/Code/tp-libvirt
    [libvirt]
    subdir: libvirt/
    [libguestfs]
    subdir: libguestfs/
    [lvsb]
    subdir: lvsb/
    [v2v]
    subdir: v2v/

   You can optionally delete temporarily the
   `io-github-autotest-qemu.ini` file, just so you don't have test
   conflicts. Then you can develop your new test code, run it
   using virt test, and commit your changes.

4. Make sure you have `inspektor installed. <https://github.com/autotest/inspektor#inspektor>`_
5. Run::

    inspekt checkall --disable-style E501,E265,W601,E402,E722,E741 --no-license-check <test_script_name>.py

6. Ensure <https://github.com/autotest/tp-libvirt/blob/master/tp-libvirt_review_comment_summary.rst> are met
7. Fix any problems
8. Push your changes and submit a pull request
9. That's it.

.. [1] You can always create your own test provider, if you have special purposes, or just want to develop your work independently.
