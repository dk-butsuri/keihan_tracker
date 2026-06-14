from setuptools import setup, find_packages

NAME = "keihan_tracker"
DESCRIPTION = "京阪電車・バスの非公式接近情報API解析ライブラリ"
AUTHOR = "dk-butsuri"
VERSION = "2.2.1"
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
CLASSIFIERS = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Development Status :: 5 - Production/Stable",
    "Intended Audience :: Developers",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Internet :: WWW/HTTP",
]

with open("README.md", encoding="utf-8") as f:
    LONG_DESCRIPTION = f.read()

setup(name=NAME,
      author=AUTHOR,
      maintainer=AUTHOR,
      description=DESCRIPTION,
      long_description=LONG_DESCRIPTION,
      long_description_content_type="text/markdown",
      version=VERSION,
      url=URL,
      download_url=URL,
      packages=find_packages(),
      python_requires=PYTHON_REQUIRES,
      install_requires=INSTALL_REQUIRES,
      license=LICENSE,
      classifiers=CLASSIFIERS,
      keywords=["keihan","train","parse","parsing","validation","tracking","position","京阪","鉄道","位置"],
      project_urls={
          "Bug Tracker": "https://github.com/dk-butsuri/keihan_tracker/issues",
          "Source": "https://github.com/dk-butsuri/keihan_tracker",
      },
    )