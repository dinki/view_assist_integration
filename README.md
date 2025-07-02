# Welcome

Welcome to the long awaited, much anticipated View Assist integration!  

A HUGE thank you goes out to Mark Parker @msp1974 for his MASSIVE help with making this a reality.  Mark has written the majority of the integration with my guidance.  You should check out his [Home Assistant Integration Examples](https://github.com/msp1974/HAIntegrationExamples) Github if you are intestered in creating your own integration.  His work has propelled View Assist to first class in very short order.  We would not be where we are today without his continued efforts and the hours and hours he has put in to make View Assist better!  Thanks again Mark!


# Installing the View Assist Integration

[![Image](https://img.youtube.com/vi/cYqGGknf4C8/mqdefault.jpg)](https://www.youtube.com/watch?v=cYqGGknf4C8)


Watch Video: https://www.youtube.com/watch?v=cYqGGknf4C8


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
