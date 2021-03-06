angular
    .module('nzbhydraApp')
    .factory('NzbHydraControlService', NzbHydraControlService);

function NzbHydraControlService($http) {

    return {
        restart: restart,
        shutdown: shutdown
    };

    function restart() {
        return $http.get("internalapi/restart");
    }

    function shutdown() {
        return $http.get("internalapi/shutdown");
    }
}
