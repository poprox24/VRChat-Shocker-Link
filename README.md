<a id="readme-top"></a>
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![project_license][license-shield]][license-url]



<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/poprox24/VRChat-Shocker-Link">
    <img width="754" height="555" alt="image" src="https://github.com/user-attachments/assets/e5089b36-c427-4471-8a55-e2bf019e9de9" />
  </a>

<h3 align="center">VRChat Shocker Link</h3>

  <p align="center">
    This simple python program connects a VRChat avatar parameter with your PiShock or OpenShock device
    <br />
    It has chat message support, curve for intensity and a few more settings you can easily change in the UI
    <br />
    <a href="https://github.com/poprox24/VRChat-Shocker-Link/issues/new?labels=bug">Report Bug or Request Feature</a>


https://github.com/user-attachments/assets/beff6062-4739-47cd-b56c-7f491de81a68
  </p>
</div>



<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
  </ol>
</details>


<!-- GETTING STARTED -->
## Getting Started

How to setup this project

### Prerequisites

Make sure to download this program before you continue:
* [Python](https://www.python.org/downloads/)

### Installation

1. Scroll up to the top of the page
2. Click on **Code** and then **Download ZIP**
    - Alternatively you can clone this repository using git
3. Extract the ZIP anywhere on your computer
4. Open **config.yml**
5. Change the **SHOCK_PARAMETER** to the parameter you created on your VRChat avatar and set **USE_PISHOCK** to true, if using a PiShock device
6. Run **RunShockerLink.bat**
    - If using an OpenShock and the shocker doesn't react, change the **OPENSHOCK_SHOCKER_ID** in **config.yml** to the one you set on the website

<br />

### Usage

1. Most stuff is self explanatory
2. You can right click to manually input a number in the curve
3. Temporary mode disables changes and will return to last saved state once it is disabled again
4. Presets:
- Left click to load
- Right click to rename
- Middle click to default

<!-- ROADMAP -->
## Roadmap

- [x] Use OSC Query instead of normal OSC
- [ ] Rewrite with OOP in mind
- [ ] Get shocker ID automatically from OpenShock devices
- [ ] Rework UI

See the [open issues](https://github.com/poprox24/VRChat-Shocker-Link/issues) for a full list of proposed features (and known issues).



<!-- CONTRIBUTING -->
## Contributing

If you have a suggestion that would make this tool better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request



### Top contributors:

<a href="https://github.com/poprox24/VRChat-Shocker-Link/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=poprox24/VRChat-Shocker-Link" alt="contrib.rocks image" />
</a>

<br />

<!-- LICENSE -->
## License

Distributed under the MIT License. See `LICENSE.txt` for more information.



<!-- CONTACT -->
## Contact

Poprox24 - [@poprox422](https://twitter.com/poprox422) - poprox24.roxy@gmail.com

Project Link: [https://github.com/poprox24/VRChat-Shocker-Link](https://github.com/poprox24/VRChat-Shocker-Link)

<p align="right">(<a href="#readme-top">back to top</a>)</p>


[contributors-shield]: https://img.shields.io/github/contributors/poprox24/VRChat-Shocker-Link.svg?style=for-the-badge
[contributors-url]: https://github.com/poprox24/VRChat-Shocker-Link/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/poprox24/VRChat-Shocker-Link.svg?style=for-the-badge
[forks-url]: https://github.com/poprox24/VRChat-Shocker-Link/network/members
[stars-shield]: https://img.shields.io/github/stars/poprox24/VRChat-Shocker-Link.svg?style=for-the-badge
[stars-url]: https://github.com/poprox24/VRChat-Shocker-Link/stargazers
[issues-shield]: https://img.shields.io/github/issues/poprox24/VRChat-Shocker-Link.svg?style=for-the-badge
[issues-url]: https://github.com/poprox24/VRChat-Shocker-Link/issues
[license-shield]: https://img.shields.io/github/license/poprox24/VRChat-Shocker-Link.svg?style=for-the-badge
[license-url]: https://github.com/poprox24/VRChat-Shocker-Link/blob/master/LICENSE
[product-screenshot]: images/screenshot.png

