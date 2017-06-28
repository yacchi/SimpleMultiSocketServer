from setuptools import setup, find_packages

with open("README.rst") as f:
    readme = f.read()

setup(
    name='SimpleMultiSocketServer',
    version='1.0',
    packages=find_packages(),
    url='https://github.com/yacchi21/SimpleMultiSocketServer',
    license='Apache License, Version 2.0',
    author='Yasunori Fujie',
    author_email='fuji@dmgw.net',
    description='',
    long_description=readme,
)
