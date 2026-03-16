#!/bin/bash
echo "#### unloading"
# launchctl unload ~/Library/LaunchAgents/com.personalassistant.ui.plist                                                                                              10s 09:51:08
launchctl bootout gui/$(id -u)/com.personalassistant.ui
launchctl list |grep personal                                                                                                                                           09:51:41
echo "#### loading"
# launchctl load ~/Library/LaunchAgents/com.personalassistant.ui.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalassistant.ui.plist
launchctl list |grep personal
