NOTE: THIS IS NOT THE UPDATED VERSION WITH AUTO TOOLS, WHICH IS IN A PRIVATE REPOSITORY. 
this also lacks labels for zebra crossing and traffic light poles, and any further changes beyond that 


# Setup Instructions 

If anaconda: 

`conda create --name pclabel0`

`conda activate pclabel0` 

If python: 

`python -m venv .venv` 

`.\.venv\Scripts\activate.bat` 


Then first time, `pip install -r requirements.txt` 

Then just run `python -m pclabel` 

If you're opening a specific project: `python -m pclabel "streets 1"` 
