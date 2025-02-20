import logging
import os
import yaml
from pathlib import Path
from pydantic import BaseModel, NonNegativeInt, Field, NewPath, DirectoryPath

class Config(BaseModel):
    scandir: NewPath|DirectoryPath = Field(default=Path('~/Scans').expanduser())
    preferred_resolution: NonNegativeInt = Field(default=300)
    preferred_mode: str = Field(default='Gray')
    preferred_source: str = Field(default='ADF')


def load_config() -> Config:
    config_file=Path(os.environ.get('CONFIG_FILE', '~/.config/simple-webscan/config.yml')).expanduser()
    data = {}
    try:
        with open(config_file, 'r') as f:
            data = yaml.safe_load(f)
    except IOError:
        logging.info("can not read config, trying to create it ...")
        config = Config()
        try:
            config_file.parent.mkdir(exist_ok=True, parents=True)
            config_file.write_text('\n'.join([f'{k}: {v}' for k,v in Config().model_dump().items()]))
        except Exception as e:
            logging.error(e)

    except Exception as err:
        logging.exception(err)
        logging.info("invalid config, using default values")


    config = Config(**data)
    config.scandir.mkdir(exist_ok=True, parents=True)
    return config