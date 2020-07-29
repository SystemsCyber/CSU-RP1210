# CSU-RP1210
 Heavy Vehicle diagnostic prototyping and training software for ATA/TMC RP1210 compatible devices.

## Setup
Install a 32-bit version of Python onto a Windows computer.

Be sure to have pip installed.

Open the Command Prompt and confirm the Python install by typing `python`. 

If the following shows up

```Python 3.8.5 (tags/v3.8.5:580fbb0, Jul 20 2020, 15:57:54) [MSC v.1924 64 bit (AMD64)] on win32```

Then you'll have to use the windows python launcher `py -3.7` (or whatever your 32-bit version is). You should get a response at the command prompt like this:
``` 
C:\Users\Jeremy>py -3.7
Python 3.7.4 (tags/v3.7.4:e09359112e, Jul  8 2019, 19:29:22) [MSC v.1916 32 bit (Intel)] on win32
Type "help", "copyright", "credits" or "license" for more information.
>>>
```

Type `exit()` to quit the interpeter. The imortant thing is to check for 32-bit python.

### Install the Requirements
From the command prompt, navigate to the directory of this application (after you've cloned or downloaded it). Then execute the command:

`py -3.7 -m pip install -r requirements.txt`

Once the requirements are installed, run the program:

`py -3.7 -m CSU_RP1210`





