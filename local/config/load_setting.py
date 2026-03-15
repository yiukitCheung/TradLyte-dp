# config/config_loader.py
import yaml
import os

def load_setting(status = None):
    """
    Loads the Data Pipeline configuration from the settings.yaml file.
    
    Args:
        status: Optional parameter for backward compatibility, no longer used
    
    Returns:
        dict: data_pipeline_config configuration dictionary.
    """
    
    config_path = os.path.join(os.path.dirname(__file__), "settings.yaml")
    
    try:
        with open(config_path, "r") as file:
            config = yaml.safe_load(file)
        return config
    
    except FileNotFoundError:
        print("Configuration file not found. Please ensure config/settings.yaml exists.")
        raise
    
    except yaml.YAMLError as e:
        print("Error reading the YAML configuration file.")
        raise e
    
if __name__ == "__main__":
    settings = load_setting()
    print(settings)