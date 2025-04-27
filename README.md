# Welcome

Welcome to the long awaited, much anticipated View Assist integration!  Some of the notable improvements include:

* All configuration done within the integration.  The days of editing YAML files are over!
* The View Assist dashboard is now autocreated when a View Assist device with visual output is configured.  This includes assets like default images and soundfiles to be downloaded and preconfigured for use
* A new view_assist directory will be created in your config directory to hold VA dashboard and views along with an easy storage location for your images and sound files to use with View Assist
* Views can now be updated through an action call
* Users can create their own views as before and use a save view action that will store a local copy that will then be used when an autoregeneration of the dashboard action is called
* Timers and alarms survive restarts!
* The external python set_state.py and control blueprint per device are no longer needed
* Some external pyscripts have now been integrated simplifying the install process
* Full support for both BrowserMod and the new [Remote Assist Display](https://github.com/michelle-avery/remote-assist-display) has been added
* Many quality of life improvements have been added on both the user and developer facing sides

A HUGE thank you goes out to Mark Parker @msp1974 for his MASSIVE help with making this a reality.  Mark has written the majority of the integration with my guidance.  You should check out his [Home Assistant Integration Examples](https://github.com/msp1974/HAIntegrationExamples) Github if you are intestered in creating your own integration.  His work has propelled View Assist to first class in very short order.  We would not be where we are today without his continued efforts and the hours and hours he has put in to make View Assist better!  Thanks again Mark!

# Install

## HACS
* Install HACS if you have not already
* Open HACS and click three dots in right corner -> Custom Repositories -> then paste `https://github.com/dinki/view_assist_integration/` in 'Repository' and choose type 'Integration' then click 'Add'
* Now search for 'View Assist' in HACS
* Click "Add" to confirm, and then click "Download" to download and install the integration
Restart Home Assistant
* Search for "View Assist" in HACS and install then restart
* In Home Assistant go to Settings -> Devices and Services -> Add integration -> Search for View Assist and add
* Configure the device(s)

## Manual Install

This integration can be installed by downloading the [view_assist](https://github.com/dinki/view_assist_integration/tree/main/custom_components) directory into your Home Assistant /config/custom_components directory and then restart Home Assistant.  We have plans to make this easier through HACS but are waiting for acceptance.

Questions, problems, concerns?  Reach out to us on Discord or use the 'Issues' above

## Development
To develop this integration, you will need Python 3.13.0 or higher, as homeassistant 2024.2.0+ requires it.  You will also need to install the dependencies referenced in the [requirements.txt](custom_components/requirements.txt) file.
In addition, if you want to run the tests, you will need to install the dependencies referenced in the [test_requirements.txt](custom_components/test_requirements.txt) file.

# Help

Need help?  Check our [View Assist Wiki](https://dinki.github.io/View-Assist/) for the most up-to-date documentation.  You can also hop on our [View Assist Discord Server](https://discord.gg/3WXXfGAf8T) and we'll give you a boost!
