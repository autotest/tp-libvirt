import os
import time
import shutil
from avocado.utils import process


class VMLogFile():
    def __init__(self, vm_name, test, log_dir='/var/log/libvirt/qemu/', backup_old_log=False):
        """
        Initializes the VMLogFile class.

        Args:
            vm_name (str): Name of the virtual machine.
            test: Avocado test instance for logging and assertions.
            log_dir (str, optional): Directory where VM logs are stored. Defaults to '/var/log/libvirt/qemu/'.
            backup_old_log (bool, optional): Whether to back up old logs before clearing. Defaults to False.
        """
        self.vm_name = vm_name
        self.test = test
        self.log_file = os.path.join(log_dir, f'{vm_name}.log')
        self.backup_name = None
        self.backup_old_log = backup_old_log

    def clear_vm_logfile(self):
        """
        Clears the VM log file before a test run.

        If the log file exists, it will be backed up to /tmp with a timestamp (if backup_old_log is True) and then cleared.

        Returns:
            str or None: The name of the backup file if created, otherwise None.
        """

        if not os.path.exists(self.log_file):
            return None  # Log file does not exist, do nothing

        self.backup_name = self.backup_log()

        # Clear the log file
        open(self.log_file, 'w').close()
        return self.backup_name

    def check_log(self, msg, fail_if_found=True):
        """
        Checks if the log file contains a specific message or pattern.

        Args:
            msg (str or list/tuple of str): Message or pattern to search for in the log file.
            fail_if_found (bool, optional): Whether to fail the test if the message is found. Defaults to True.

        Raises:
            ValueError: If msg is not a string or a list/tuple of strings.
        """
        self.test.log.debug(f"checking for: {msg}")
        if not msg:  # Handle empty input case
            return None

        if isinstance(msg, str):
            pattern = msg
        elif isinstance(msg, (list, tuple)):
            pattern = "|".join(msg)
        else:
            raise ValueError("Invalid input type. Expected string or list/tuple of strings.")

        command = f"grep -E '{pattern}' {self.log_file}"
        res = process.run(command, ignore_status=True, shell=True)
        self.test.log.debug(f"grep result: {res}")
        if fail_if_found and res.stdout != b'':
            self.test.fail(f"Log file contains some unwanted messages: {res.stdout}.")
        if not fail_if_found and res.stdout == b'':
            self.test.fail(f"Log file doesn't contain expected messages {msg}")

    def backup_log(self):
        """
        Backs up the VM log file to /tmp with a timestamp if backup_old_log is enabled.

        Returns:
            str or None: The name of the backup file if created, otherwise None.
        """
        if self.backup_old_log:
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            self.backup_name = f'/tmp/{self.vm_name}_log_{timestamp}.log'
            shutil.copy2(self.log_file, self.backup_name)  # Create a backup with a timestamp
        return self.backup_name

    def restore_log(self):
        """
        Placeholder method for restoring the VM log file from backup.

        Raises:
            NotImplementedError: As restore functionality is not implemented yet.
        """
        if self.backup_name:
            raise NotImplementedError("Restore log not yet implemented")
