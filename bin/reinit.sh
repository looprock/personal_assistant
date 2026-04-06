#!/bin/bash -x
launchctl list |grep personal
echo "#### unloading"
launchctl bootout gui/$(id -u)/com.personalassistant.ui
sleep 1
launchctl bootout gui/$(id -u)/com.personalassistant.emailwatcher
sleep 1
launchctl bootout gui/$(id -u)/com.personalassistant.digest
sleep 1
launchctl list |grep personal
echo "#### loading"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalassistant.ui.plist
sleep 1
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalassistant.emailwatcher.plist
sleep 1
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalassistant.digest.plist
sleep 1
launchctl list |grep personal
