from setuptools import setup, find_packages

setup(
    name="memcraft",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={"memcraft": ["templates/*.md"]},
    install_requires=[],
    entry_points={
        "console_scripts": [
            "memcraft=memcraft.cli:main",
        ],
    },
)
