from setuptools import setup, find_packages

setup(
    name="memkraft",
    version="0.2.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={"memkraft": ["templates/*.md"]},
    install_requires=[],
    entry_points={
        "console_scripts": [
            "memkraft=memkraft.cli:main",
        ],
    },
)
