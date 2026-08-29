# Third-party notices

Realtime Audio Translator is licensed under the MIT License. See `LICENSE`.

## Python components

The application archive contains Python libraries and PyInstaller output. Exact
versions, upstream sources, and licenses are recorded in `SBOM.cdx.json`.
`THIRD_PARTY_LICENSES.txt` contains the license files available in the exact
packages used by the release build, including notices for native libraries
bundled inside wheels (for example OpenBLAS, LAPACK, and the GCC runtime in
NumPy). Those native files are tracked as part of their exact Python
distribution in the SBOM.

The CTranslate2 wheel does not contain its license file, so its upstream MIT
notice is reproduced here:

> Copyright (c) 2018- SYSTRAN. Copyright (c) 2019- The OpenNMT Authors.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Offline translation models

Official releases do not redistribute Argos model packages. The Chinese and
English packages only state a license for their original OPUS model, while the
Japanese and Korean packages do not state a model license. This is not enough
to establish redistribution terms for each complete `.argosmodel` artifact.
Users obtain models directly from Argos through the application and remain
subject to the publisher's terms.

## External speech runtime

Official releases do not redistribute Faster-Whisper-XXL, CUDA, cuBLAS, or
cuDNN files. The Faster-Whisper-XXL repository does not publish a repository
license, and NVIDIA permits redistribution only under its applicable SDK terms.
Users obtain these components directly from their publishers and remain subject
to the publishers' terms:

- Faster-Whisper-XXL: https://github.com/Purfview/whisper-standalone-win/releases
- NVIDIA SDK license: https://docs.nvidia.com/cuda/eula/index.html
- NVIDIA cuDNN license: https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html

This notice is an inventory and attribution record, not legal advice.
