# -*- coding: utf-8 -*-
#
# Aruba CX NAE Script - Weekly Scheduled TFTP Backup
#
# This script performs automated backups of the switch running configuration
# to a TFTP server on a specific day of the week at a scheduled time.

"""
Weekly Scheduled TFTP Backup Agent

This NAE agent backs up the switch running configuration to a TFTP server
on a specified day of the week at a specified time. The backup is stored 
with a timestamped filename.

Parameters:
    - tftp_server_address: IP address or hostname of the TFTP server
    - tftp_server_vrf: VRF to reach the TFTP server (default: mgmt)
    - tftp_configuration_format: Format for backup (cli or json, default: json)
    - file_name_prefix: Prefix for backup filename (timestamp will be appended)
    - backup_day_of_week: Day of week for backup (Monday, Tuesday, etc.)
    - backup_time: Time of day for backup in HH:MM:SS format (default: 02:30:00)
"""

import time
from datetime import datetime

Manifest = {
    'Name': 'weekly_tftp_backup',
    'Description': 'Backs up switch configuration to TFTP server weekly on scheduled day/time',
    'Version': '1.5',
    'Author': 'Matthew Stegink',
    'AOSCXVersionMin': '10.08',
    'AOSCXPlatformList': ['8320', '8325', '8400', '6300', '6200']
}

ParameterDefinitions = {
    'tftp_server_address': {
        'Name': 'TFTP Server Address',
        'Description': 'IP address or hostname of the TFTP server',
        'Type': 'string',
        'Default': ''
    },
    'tftp_server_vrf': {
        'Name': 'VRF Name',
        'Description': 'VRF through which the TFTP server can be reached',
        'Type': 'string',
        'Default': 'mgmt'
    },
    'tftp_configuration_format': {
        'Name': 'Configuration Format',
        'Description': 'Format for configuration backup (cli or json)',
        'Type': 'string',
        'Default': 'json'
    },
    'file_name_prefix': {
        'Name': 'Filename Prefix',
        'Description': 'Prefix for backup filename (timestamp will be appended)',
        'Type': 'string',
        'Default': 'switch-backup-'
    },
    'backup_day_of_week': {
        'Name': 'Backup Day of Week',
        'Description': 'Day of week for backup (Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday)',
        'Type': 'string',
        'Default': 'Sunday'
    },
    'backup_time': {
        'Name': 'Backup Time',
        'Description': 'Time of day for backup in HH:MM:SS format (24-hour)',
        'Type': 'string',
        'Default': '02:30:00'
    }
}


class Agent(NAE):
    """
    NAE Agent for weekly scheduled TFTP backups.
    
    This agent monitors the time and day of week, triggering a backup when 
    the scheduled day and time is reached. It ensures only one backup occurs 
    per week.
    """
    
    # Map day names to numbers (0=Monday, 6=Sunday)
    DAY_MAP = {
        'monday': 0,
        'tuesday': 1,
        'wednesday': 2,
        'thursday': 3,
        'friday': 4,
        'saturday': 5,
        'sunday': 6
    }
    
    def __init__(self):
        """Initialize the weekly scheduled backup agent."""
        # Create a rule that checks every 60 seconds
        self.schedule_rule = Rule('Weekly Backup Check')
        self.schedule_rule.condition('every 60 seconds')
        self.schedule_rule.action(self.check_backup_schedule)
        
        self.logger.info("Weekly Scheduled TFTP Backup Agent initialized")

    def check_backup_schedule(self, event):
        """
        Check if it's time to run the weekly backup.
        
        This method is called every 60 seconds. It checks if today is the 
        scheduled day, if the current time has passed the scheduled backup 
        time, and if a backup hasn't already been performed this week.
        
        :param event: Event details passed by the NAE agent
        """
        try:
            current_datetime = datetime.now()
            current_time = current_datetime.strftime("%H:%M:%S")
            current_day = current_datetime.weekday()  # 0=Monday, 6=Sunday
            current_week = current_datetime.strftime('%Y-W%U')  # Year-Week number
            
            # Get and validate scheduled day
            scheduled_day_name = str(self.params['backup_day_of_week']).strip().lower()
            if scheduled_day_name not in self.DAY_MAP:
                self.logger.error(f"Invalid day: {scheduled_day_name}. Use Monday-Sunday")
                return
            
            scheduled_day = self.DAY_MAP[scheduled_day_name]
            
            # Get and validate scheduled time
            scheduled_time = str(self.params['backup_time']).strip()
            if not self._is_valid_time_format(scheduled_time):
                self.logger.error(f"Invalid time format: {scheduled_time}. Use HH:MM:SS")
                return
            
            # Check if backup already ran this week
            last_backup_week = self.variables.get('last_backup_week', '')
            
            # If today is the scheduled day, time has passed, and we haven't backed up this week
            if (current_day == scheduled_day and 
                current_time >= scheduled_time and 
                last_backup_week != current_week):
                
                day_name = scheduled_day_name.capitalize()
                self.logger.info(f"Weekly backup triggered on {day_name} at {current_time}")
                self.perform_backup()
                self.variables['last_backup_week'] = current_week
                
        except Exception as e:
            self.logger.error(f"Error in check_backup_schedule: {e}")

    def perform_backup(self):
        """
        Perform the actual TFTP backup operation.
        
        Validates parameters and executes the TFTP copy command to back up
        the running configuration.
        """
        try:
            # Validate TFTP server address
            tftp_server = str(self.params['tftp_server_address']).strip()
            if not tftp_server:
                self.logger.error("TFTP server address not configured")
                ActionSyslog("Weekly backup failed: TFTP server not configured", 
                           severity='WARNING')
                return
            
            # Validate filename prefix
            file_prefix = str(self.params['file_name_prefix']).strip()
            if not file_prefix:
                self.logger.error("Filename prefix not configured")
                ActionSyslog("Weekly backup failed: Filename prefix not configured",
                           severity='WARNING')
                return
            
            # Get and validate format
            config_format = str(self.params['tftp_configuration_format']).strip().lower()
            if config_format not in ('json', 'cli'):
                self.logger.warning(f"Invalid format '{config_format}', using 'json'")
                config_format = 'json'
            
            # Get VRF
            vrf = str(self.params['tftp_server_vrf']).strip()
            
            # Execute backup
            self.logger.info(f"Starting weekly backup to TFTP server {tftp_server}")
            self._tftp_copy(tftp_server, file_prefix, vrf, config_format)
            
            ActionSyslog(f"Weekly configuration backup completed to {tftp_server}",
                       severity='INFO')
            
        except Exception as e:
            self.logger.error(f"Backup failed: {e}")
            ActionSyslog(f"Weekly backup failed: {e}", severity='ERR')

    def _tftp_copy(self, tftp_server, file_prefix, vrf, config_format):
        """
        Execute the TFTP copy command.
        
        :param tftp_server: IP address or hostname of TFTP server
        :param file_prefix: Prefix for the backup filename
        :param vrf: VRF name to reach the TFTP server
        :param config_format: Format for backup (json or cli)
        """
        # Generate timestamped filename
        timestamp = int(time.time())
        file_extension = '.json' if config_format == 'json' else '.cfg'
        filename = f"{file_prefix}{timestamp}{file_extension}"
        
        # Build TFTP command
        tftp_command = f'copy running-config tftp://{tftp_server}/{filename} {config_format}'
        
        if vrf:
            tftp_command += f' vrf {vrf}'
        
        self.logger.info(f"Executing: {tftp_command}")
        ActionCLI(tftp_command)

    def _is_valid_time_format(self, time_str):
        """
        Validate time string is in HH:MM:SS format.
        
        :param time_str: Time string to validate
        :return: True if valid, False otherwise
        """
        try:
            parts = time_str.split(':')
            if len(parts) != 3:
                return False
            
            hour, minute, second = map(int, parts)
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                return False
            
            return True
        except (ValueError, AttributeError):
            return False
