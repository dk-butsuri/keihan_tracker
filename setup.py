from setuptools import setup, find_packages

NAME = "keihan_tracker"
DESCRIPTION = "京阪電車・バスの非公式接近情報API解析ライブラリ"
AUTHOR = "dk-butsuri"
VERSION = "2.1.3"
PYTHON_REQUIRES = ">=3.10"
URL = "https://github.com/dk-butsuri/keihan_tracker"
INSTALL_REQUIRES = [
    "pydantic",
    "httpx",
    "tabulate",
    "beautifulsoup4",
    "tzdata"
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