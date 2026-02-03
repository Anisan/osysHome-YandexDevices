# YandexDevices - Yandex IoT Devices Integration

![YandexDevices Icon](static/YandexDevices.png)

Integration with Yandex IoT (Quasar) platform for managing Yandex smart devices and stations.

## Description

The `YandexDevices` module provides integration with Yandex IoT platform (Quasar) for the osysHome platform. It enables control and monitoring of Yandex smart devices including Yandex Stations, smart speakers, and IoT devices.

## Main Features

- ✅ **Device Management**: Manage Yandex IoT devices
- ✅ **Station Control**: Control Yandex Stations
- ✅ **Capability Management**: Manage device capabilities
- ✅ **OAuth Authentication**: QR code-based authentication
- ✅ **Property Linking**: Link device capabilities to object properties
- ✅ **Method Linking**: Link device commands to object methods
- ✅ **Search Integration**: Search devices and stations
- ✅ **Widget Support**: Dashboard widget

## Admin Panel

The module provides an admin interface for:
- Viewing Yandex devices
- Managing Yandex Stations
- Configuring device capabilities
- Linking capabilities to properties

## Authentication

The module uses QR code authentication:
1. Navigate to authentication page
2. Scan QR code with Yandex app
3. Confirm authorization
4. Devices discovered automatically

## Configuration

- **Device Token**: Per-station device tokens
- **Platform**: Device platform type
- **Capabilities**: Device capabilities configuration

## Usage

### Adding Station

1. Navigate to YandexDevices module
2. Authenticate via QR code
3. Stations discovered automatically
4. Generate device token for station
5. Link capabilities to object properties

## Technical Details

- **API**: Yandex Quasar API
- **Authentication**: OAuth with QR code
- **Device Types**: Yandex Stations, smart speakers, IoT devices
- **Capabilities**: on/off, volume, brightness, etc.

## Version

Current version: **0.2**

## Category

Devices

## Actions

The module provides the following actions:
- `cycle` - Background device monitoring
- `say` - Send messages via Yandex Station
- `widget` - Dashboard widget

## Requirements

- Flask
- SQLAlchemy
- Requests
- osysHome core system

## Author

Eraser

## License

See the main osysHome project license

