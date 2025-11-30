from setuptools import setup, find_packages
NAME = "keihan_tracker"
DESCRIPTION = "京阪電車の列車位置APIラッパー"
AUTHOR = "dk-butsuri"
VERSION = "1.2.0"
PYTHON_REQUIRES = ">=3.10"
URL = "https://github.com/dk-butsuri/keihan_tracker"
INSTALL_REQUIRES = [
    "pydantic",
    "httpx",
    "tabulate",
    "beautifulsoup4"
    ]
LICENSE = "MIT License"

setup(name=NAME,
      author=AUTHOR,
      maintainer=AUTHOR,
      description=DESCRIPTION,
      version=VERSION,
      url=URL,
      download_url=URL,
      packages=find_packages(),
      python_requires=PYTHON_REQUIRES,
      install_requires=INSTALL_REQUIRES,
      license=LICENSE
    )