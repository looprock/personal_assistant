#!/bin/bash
launchctl kickstart gui/$(id -u)/com.personalassistant.emailwatcher && tail -f /tmp/pa-emailwatcher.log
