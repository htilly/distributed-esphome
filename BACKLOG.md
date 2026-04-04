1. Allow compiling on VPS servers (not on the same network)
   https://www.reddit.com/r/Esphome/comments/1s9wscs/comment/odwqlsb/?utm_source=share&utm_medium=web3x&utm_name=web3xcss&utm_term=1&utm_content=share_button

   Maybe GitHub actions? 

2. Introduce GitHub functionality for the configs

3. Need to manage disk space on clients to make sure we don't run out 

4. Ability to dump firmware off a device. 

5. DONE (1.2.0-dev.3) - Manually choose a worker. "Upgrade on..." submenu in device hamburger lists online workers. Jobs pinned via pinned_client_id.

6. https://github.com/weirded/distributed-esphome/issues/4 Worker on server (configurable)

7. DONE (1.2.0-dev.2) - Upgrade All now skips known-offline devices. Includes online + checking/unknown, excludes confirmed offline.

8. DONE (1.2.0-dev.1) - Change parallel job slots from the web UI. Workers tab has +/- controls per worker; value pushed via heartbeat, worker restarts to apply.

9. DONE (1.2.0-dev.1) - Queue now shows friendly device names (matching Devices tab) with filename and timestamp below.

10. Edit yaml files in subfolders 

11. DONE (1.2.0-dev.3) - Docker Compose file added: docker-compose.worker.yml

12. For the devices page, I want to show additional attributes of the configuration, and allow used to select which columns to show/hide (with the name, status and buttons always there). I'm talking name, friendly_name, area, comment, project, number of subdevices (if any), 